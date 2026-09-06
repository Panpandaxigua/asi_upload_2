# -*- coding: utf-8 -*-
import unittest

import pandas as pd

from upload_controls import (
    default_upload_controls,
    field_mode,
    filter_row_for_module,
    module_enabled,
    module_should_run,
    normalize_upload_controls,
    runtime_switch_value,
    validate_required_fields,
)


class UploadControlPolicyTests(unittest.TestCase):
    def test_defaults_preserve_all_existing_modules(self):
        controls = default_upload_controls()

        self.assertTrue(module_enabled(controls, "basic_details"))
        self.assertTrue(module_enabled(controls, "attributes"))
        self.assertTrue(module_enabled(controls, "imprint"))
        self.assertTrue(module_enabled(controls, "pricing"))
        self.assertTrue(module_enabled(controls, "media"))
        self.assertEqual(field_mode(controls, "attributes", "materials"), "auto")

    def test_normalization_ignores_unknown_modules_fields_and_modes(self):
        controls = normalize_upload_controls({
            "modules": {
                "pricing": {
                    "enabled": False,
                    "fields": {
                        "price_tiers": "skip",
                        "price_codes": "invalid-mode",
                        "unknown": "required",
                    },
                },
                "unknown": {"enabled": False},
            }
        })

        self.assertFalse(module_enabled(controls, "pricing"))
        self.assertEqual(field_mode(controls, "pricing", "price_tiers"), "skip")
        self.assertEqual(field_mode(controls, "pricing", "price_codes"), "auto")
        self.assertNotIn("unknown", controls["modules"])

    def test_skip_blanks_every_alias_without_mutating_dict_source(self):
        row = {"Material": "Polyester", "Material Custom Name": "Eco Fabric"}
        controls = default_upload_controls()
        controls["modules"]["attributes"]["fields"]["materials"] = "skip"

        filtered = filter_row_for_module(row, controls, "attributes")

        self.assertEqual(filtered["Material"], "")
        self.assertEqual(filtered["Material Custom Name"], "")
        self.assertEqual(row["Material"], "Polyester")
        self.assertEqual(row["Material Custom Name"], "Eco Fabric")

    def test_skip_blanks_normalized_aliases_on_series(self):
        row = pd.Series({"Product Color": "Blue", "Other": "keep"})
        controls = default_upload_controls()
        controls["modules"]["attributes"]["fields"]["colors"] = "skip"

        filtered = filter_row_for_module(row, controls, "attributes")

        self.assertEqual(filtered["Product Color"], "")
        self.assertEqual(filtered["Other"], "keep")
        self.assertEqual(row["Product Color"], "Blue")

    def test_required_price_tiers_reports_label_when_no_complete_pair_exists(self):
        controls = default_upload_controls()
        controls["modules"]["pricing"]["fields"]["price_tiers"] = "required"

        issues = validate_required_fields(
            {"Product_Number": "P1", "Q1": "100", "P1": ""},
            controls,
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("价格档位", issues[0])

    def test_required_price_tiers_accepts_one_complete_pair(self):
        controls = default_upload_controls()
        controls["modules"]["pricing"]["fields"]["price_tiers"] = "required"

        issues = validate_required_fields(
            {"Product_Number": "P1", "Q1": "100", "P1": "12.50"},
            controls,
        )

        self.assertEqual(issues, [])

    def test_disabled_module_never_runs(self):
        controls = default_upload_controls()
        controls["modules"]["attributes"]["enabled"] = False

        self.assertFalse(module_should_run({"Material": "Polyester"}, controls, "attributes"))

    def test_enabled_module_with_only_skipped_fields_does_not_run(self):
        controls = default_upload_controls()
        for field in controls["modules"]["pricing"]["fields"]:
            controls["modules"]["pricing"]["fields"][field] = "skip"

        self.assertFalse(module_should_run({"Q1": "100", "P1": "12"}, controls, "pricing"))

    def test_runtime_media_field_keeps_legacy_module_active(self):
        controls = default_upload_controls()

        self.assertTrue(module_should_run({}, controls, "media"))

    def test_confirmed_runtime_switches_have_persisted_boolean_values(self):
        controls = default_upload_controls()

        self.assertTrue(runtime_switch_value(controls, "basic_details", "prop65"))
        self.assertTrue(runtime_switch_value(controls, "imprint", "unimprinted"))
        self.assertTrue(runtime_switch_value(controls, "pricing", "order_less_than_minimum"))

        controls["modules"]["imprint"]["values"]["unimprinted"] = False
        normalized = normalize_upload_controls(controls)
        self.assertFalse(runtime_switch_value(normalized, "imprint", "unimprinted"))


if __name__ == "__main__":
    unittest.main()
