# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from asi_field_sync import install_field_registry, install_live_options
from asi_bot import AsiBot


class AsiFieldSyncTests(unittest.TestCase):
    def test_run_js_safe_forwards_positional_arguments(self):
        class FakeTab:
            def run_js(self, script, *args, **kwargs):
                return {"script": script, "args": args, "kwargs": kwargs}

        bot = AsiBot("65991", "user", "password")
        bot.tab = FakeTab()
        result = bot._run_js_safe("return arguments[0]", "product-info-view")
        self.assertEqual(result["args"], ("product-info-view",))

    def test_bot_scans_rendered_tabs_of_an_existing_product(self):
        bot = AsiBot("65991", "user", "password")
        bot.tab = SimpleNamespace(url="https://espupdates.asicentral.com/#/dashboard")
        captures = {
            "basic_details": {"fields": [{"key": "basic:name", "module": "basic_details", "label": "Name", "type": "text"}]},
            "attributes": {"fields": [{"key": "attributes:material", "module": "attributes", "label": "Material", "type": "text"}]},
            "imprint": {"fields": [{"key": "imprint:method", "module": "imprint", "label": "Method", "type": "select-one"}]},
            "pricing": {"fields": [{"key": "pricing:quantity1", "module": "pricing", "label": "Quantity 1", "type": "text"}]},
            "media": {"fields": [{"key": "media:imagePath", "module": "media", "label": "Image", "type": "file"}]},
            "sku": {"fields": [{"key": "sku:value", "module": "sku", "label": "SKU", "type": "text"}]},
            "availability": {"fields": [{"key": "availability:date", "module": "availability", "label": "Date", "type": "text"}]},
        }
        visited = []

        def navigate(route, root_id):
            visited.append((route, root_id))
            return {"controls": 1, "visible": True}

        def capture(include_static=False):
            module = visited[-1][1].replace("product-", "").replace("-view", "")
            module = {"info": "basic_details", "images": "media"}.get(module, module)
            return {
                "source_url": "https://espupdates.asicentral.com/#" + visited[-1][0],
                "source_scripts": ["https://espupdates.asicentral.com/bundles/asiapplibs?v=current"],
                **captures[module],
            }

        with patch.object(bot, "_open_existing_product_for_field_scan") as open_mock, \
                patch.object(bot, "_navigate_field_scan_tab", side_effect=navigate), \
                patch.object(bot, "_capture_optional_fields_from_current_page", side_effect=capture):
            result = bot.collect_optional_field_registry()

        open_mock.assert_called_once_with()
        self.assertEqual(len(visited), 7)
        self.assertEqual(len(result["fields"]), 7)
        self.assertEqual(result["scan_mode"], "existing_product_rendered_tabs")
        self.assertIn("#/product/pricing", result["scanned_routes"])

    def test_bot_collects_live_option_groups_from_asi_lookup_apis(self):
        bot = AsiBot("65991", "user", "password")
        responses = {
            "/api/api/lookup/product_types": [{"DisplayName": "Drinkware"}],
            "/api/api/lookup/themes": [{"SetCodeValues": [{"CodeValue": "Academic"}]}],
            "/api/api/Lookup/countries": [{"DisplayName": "UNITED STATES"}],
            "/api/api/lookup/packaging": [{"Value": "Box"}],
            "/api/api/lookup/colors": [{"DisplayName": "Blue", "CodeValueGroups": [{"SetCodeValues": [{"CodeValue": "Navy Blue"}]}]}],
            "/api/api/lookup/materials": [{"DisplayName": "Fabric", "MajorCodeValueGroups": [{"DisplayName": "Cotton"}]}],
            "/api/api/lookup/imprint_methods": [{"CodeValue": "Silkscreen"}],
            "/api/api/Lookup/discount_rates?q=s": [{"IndustryDiscountCode": "P", "DiscountPercent": 0.5}],
            "/api/api/Lookup/sizes": [{
                "DisplayName": "Dimension(L,W,H)",
                "CodeValueGroups": [{"DisplayName": "Length", "SetCodeValues": [{"DisplayName": "1 inch"}]}],
            }],
        }
        with patch.object(bot, "api_request", side_effect=lambda method, url: responses[url]):
            catalog = bot.collect_live_option_catalog()

        self.assertEqual(catalog["product_type"], ["Drinkware"])
        self.assertEqual(catalog["product_theme"], ["Academic"])
        self.assertEqual(catalog["product_color"], ["Blue", "Navy Blue"])
        self.assertEqual(catalog["material"], ["Fabric", "Cotton"])
        self.assertEqual(catalog["price_code"], ["A/P 50%"])
        self.assertEqual(catalog["setup_charge_code"], ["A/P 50%"])
        self.assertEqual(catalog["select_size"], ["Dimension(L,W,H)"])

    def test_install_registry_reports_added_removed_and_changed_fields(self):
        first = {
            "source_url": "https://espupdates.asicentral.com/#/dashboard",
            "source_scripts": ["/bundles/asiapplibs?v=one"],
            "fields": [
                {"key": "basic:distOnlyView", "module": "basic_details", "label": "Distributor Only View", "type": "checkbox", "binding": "distOnlyView"},
                {"key": "pricing:currency", "module": "pricing", "label": "Currency", "type": "select", "binding": "currency", "options": ["USD"]},
            ],
            "scan_mode": "existing_product_rendered_tabs",
            "scanned_routes": ["#/product/info", "#/product/pricing"],
        }
        second = {
            "source_url": first["source_url"],
            "source_scripts": ["/bundles/asiapplibs?v=two"],
            "scan_mode": "existing_product_rendered_tabs",
            "scanned_routes": first["scanned_routes"],
            "fields": [
                {"key": "pricing:currency", "module": "pricing", "label": "Currency", "type": "select", "binding": "currency", "options": ["USD", "CAD"]},
                {"key": "media:altText", "module": "media", "label": "Alt Text", "type": "text", "binding": "altText"},
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "asi_optional_fields.json"
            initial = install_field_registry(first, target)
            updated = install_field_registry(second, target)

            self.assertEqual(initial["added"], 2)
            self.assertEqual(initial["removed"], 0)
            self.assertEqual(initial["changed"], 0)
            self.assertEqual(updated["added"], 1)
            self.assertEqual(updated["removed"], 1)
            self.assertEqual(updated["changed"], 1)
            saved = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(saved["field_count"], 2)
            self.assertTrue(saved["fingerprint"])
            self.assertEqual(saved["scan_mode"], "existing_product_rendered_tabs")

    def test_install_registry_rejects_empty_capture_without_overwriting_cache(self):
        valid = {
            "source_url": "https://espupdates.asicentral.com/#/dashboard",
            "fields": [{"key": "basic:name", "module": "basic_details", "label": "Name", "type": "text"}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "asi_optional_fields.json"
            install_field_registry(valid, target)
            before = target.read_text(encoding="utf-8")
            with self.assertRaises(ValueError):
                install_field_registry({"source_url": valid["source_url"], "fields": []}, target)
            self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_live_options_update_values_but_preserve_rules_and_excel_mapping(self):
        bundled = {
            "product_type": {"label": "Product Type", "excel_column": "Product_Type", "options": ["Old Type"]},
            "material": {"label": "Material", "multi": True, "excel_column": "Material", "options": ["Old Material"]},
            "imprint_location": {"label": "Location", "options": ["Back"]},
            "validation_rules": {"description_format": {"rules": [{"type": "max_length", "value": 800}]}},
        }
        catalog = {
            "product_type": ["Drinkware", "Bags & Luggage"],
            "material": ["Cotton", "Polyester"],
            "imprint_location": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_path = root / "bundled.json"
            target = root / "cache" / "asi_options.json"
            bundled_path.write_text(json.dumps(bundled), encoding="utf-8")
            result = install_live_options(catalog, bundled_path, target)
            saved = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(result["updated_groups"], 2)
        self.assertEqual(saved["product_type"]["options"], ["Old Type", *catalog["product_type"]])
        self.assertEqual(saved["material"]["options"], ["Old Material", *catalog["material"]])
        self.assertEqual(saved["product_type"]["excel_column"], "Product_Type")
        self.assertEqual(saved["imprint_location"]["options"], ["Back"])
        self.assertEqual(saved["validation_rules"], bundled["validation_rules"])


if __name__ == "__main__":
    unittest.main()
