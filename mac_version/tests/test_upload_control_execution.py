# -*- coding: utf-8 -*-
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import asi_bot
from asi_bot import AsiBot
from product_flags import default_product_flags
from upload_controls import default_upload_controls


class UploadControlExecutionTests(unittest.TestCase):
    def _stub_bot(self):
        bot = AsiBot.__new__(AsiBot)
        bot.navigate_to_add_product = Mock()
        bot.create_product = Mock(return_value=True)
        bot.wait_product_detail_ready = Mock()
        bot.get_current_product_id = Mock(return_value="123")
        bot.get_product_json = Mock(return_value={})
        bot.apply_basic_product_json = Mock(side_effect=lambda row, value: value)
        bot.save_product_json = Mock()
        bot.fill_basic_details = Mock()
        bot.fill_attributes = Mock()
        bot.fill_imprint = Mock()
        bot.fill_pricing = Mock()
        bot.fill_media = Mock(return_value={"success": True, "message": "ok"})
        bot.save_current_tab = Mock(return_value=None)
        bot.make_product_active = Mock(return_value=None)
        return bot

    @staticmethod
    def _row(**overrides):
        row = {
            "Product_Number": "P100",
            "Product_Name": "Controlled Product",
            "Product_Type": "Bags",
            "Description": "Description that may be skipped",
            "Product Color": "Blue",
            "Material": "Polyester",
            "Material Custom Name": "Eco Fabric",
            "Select Method": "Silkscreen",
            "Q1": "100",
            "P1": "12.50",
        }
        row.update(overrides)
        return row

    def test_process_product_does_not_touch_disabled_modules(self):
        bot = self._stub_bot()
        controls = default_upload_controls()
        controls["modules"]["attributes"]["enabled"] = False
        controls["modules"]["imprint"]["enabled"] = False
        controls["modules"]["pricing"]["enabled"] = False
        controls["modules"]["media"]["enabled"] = False

        result = bot.process_product(
            self._row(),
            image_root_path="images",
            make_active=False,
            upload_controls=controls,
        )

        self.assertEqual(result, ("success", ""))
        bot.fill_basic_details.assert_called_once()
        bot.fill_attributes.assert_not_called()
        bot.fill_imprint.assert_not_called()
        bot.fill_pricing.assert_not_called()
        bot.fill_media.assert_not_called()
        bot.save_current_tab.assert_called_once_with("Basic Details")

    def test_process_product_forwards_product_flags_to_basic_and_imprint(self):
        bot = self._stub_bot()
        controls = default_upload_controls()
        controls["modules"]["attributes"]["enabled"] = False
        controls["modules"]["pricing"]["enabled"] = False
        controls["modules"]["media"]["enabled"] = False
        flags = default_product_flags()
        flags["seo_product"] = True
        flags["unimprinted"] = False

        result = bot.process_product(
            self._row(),
            make_active=False,
            upload_controls=controls,
            product_flags=flags,
        )

        self.assertEqual(result, ("success", ""))
        self.assertEqual(bot.fill_basic_details.call_args.kwargs["product_flags"], flags)
        self.assertEqual(bot.fill_imprint.call_args.kwargs["product_flags"], flags)

    def test_skipped_materials_are_blank_before_attributes_filler(self):
        bot = self._stub_bot()
        controls = default_upload_controls()
        controls["modules"]["basic_details"]["enabled"] = False
        controls["modules"]["imprint"]["enabled"] = False
        controls["modules"]["pricing"]["enabled"] = False
        controls["modules"]["media"]["enabled"] = False
        controls["modules"]["attributes"]["fields"]["materials"] = "skip"
        source = self._row()
        captured = {}
        bot.fill_attributes.side_effect = lambda row: captured.update(dict(row))

        result = bot.process_product(
            source,
            image_root_path="images",
            make_active=False,
            upload_controls=controls,
        )

        self.assertEqual(result, ("success", ""))
        self.assertEqual(captured["Material"], "")
        self.assertEqual(captured["Material Custom Name"], "")
        self.assertEqual(captured["Product Color"], "Blue")
        self.assertEqual(source["Material"], "Polyester")
        bot.save_current_tab.assert_called_once_with("Attributes")

    def test_required_control_fails_before_opening_add_product(self):
        bot = self._stub_bot()
        controls = default_upload_controls()
        controls["modules"]["pricing"]["fields"]["price_tiers"] = "required"

        result = bot.process_product(
            self._row(Q1="", P1=""),
            make_active=False,
            upload_controls=controls,
        )

        self.assertEqual(result[0], "fail")
        self.assertIn("价格档位", result[1])
        bot.navigate_to_add_product.assert_not_called()

    def test_basic_details_skip_does_not_apply_distributor_or_prop65(self):
        bot = AsiBot.__new__(AsiBot)
        bot.tab = object()
        bot.set_distributor_only_view = Mock()
        bot.fill_basic_text_fields = Mock(return_value=None)
        bot.fill_basic_origins = Mock()
        bot.fill_basic_shipping = Mock()
        controls = default_upload_controls()
        controls["modules"]["basic_details"]["fields"]["distributor_only_view"] = "skip"
        controls["modules"]["basic_details"]["fields"]["prop65"] = "skip"

        with patch.object(asi_bot, "click_tab"):
            bot.fill_basic_details(
                self._row(),
                distributor_only_view=True,
                upload_controls=controls,
            )

        bot.set_distributor_only_view.assert_not_called()
        bot.fill_basic_text_fields.assert_called_once()
        self.assertFalse(bot.fill_basic_text_fields.call_args.kwargs["include_prop65"])

    def test_prop65_runtime_switch_can_explicitly_clear_no_chemicals(self):
        bot = AsiBot.__new__(AsiBot)
        bot.tab = object()
        bot.set_distributor_only_view = Mock()
        bot.set_prop65_no_chemicals = Mock(return_value={"noChemicals": False})
        bot.fill_basic_text_fields = Mock(return_value=None)
        bot.fill_basic_origins = Mock()
        bot.fill_basic_shipping = Mock()
        controls = default_upload_controls()
        controls["modules"]["basic_details"]["values"]["prop65"] = False

        with patch.object(asi_bot, "click_tab"):
            bot.fill_basic_details(self._row(), upload_controls=controls)

        bot.set_prop65_no_chemicals.assert_called_once_with(False)
        self.assertFalse(bot.fill_basic_text_fields.call_args.kwargs["include_prop65"])

    def test_product_flags_control_basic_header_and_prop65_switches(self):
        bot = AsiBot.__new__(AsiBot)
        bot.tab = object()
        bot.set_basic_product_flags = Mock(return_value={"ok": True})
        bot.set_prop65_no_chemicals = Mock(return_value={"noChemicals": False})
        bot.fill_basic_text_fields = Mock(return_value=None)
        bot.fill_basic_origins = Mock()
        bot.fill_basic_shipping = Mock()
        flags = default_product_flags()
        flags["prop65_no_chemicals"] = False

        with patch.object(asi_bot, "click_tab"):
            bot.fill_basic_details(self._row(), product_flags=flags)

        bot.set_basic_product_flags.assert_called_once_with(flags)
        bot.set_prop65_no_chemicals.assert_called_once_with(False)
        self.assertFalse(bot.fill_basic_text_fields.call_args.kwargs["include_prop65"])

    def test_imprint_skip_unimprinted_does_not_touch_page_for_empty_row(self):
        class FakeTab:
            def __init__(self):
                self.calls = []

            def run_js(self, *args):
                self.calls.append(args)
                return {}

        bot = AsiBot.__new__(AsiBot)
        bot.tab = FakeTab()
        controls = default_upload_controls()
        controls["modules"]["imprint"]["fields"]["unimprinted"] = "skip"

        with patch.object(asi_bot, "click_tab"), patch.object(asi_bot.time, "sleep"):
            bot.fill_imprint({}, upload_controls=controls)

        self.assertEqual(bot.tab.calls, [])

    def test_imprint_unimprinted_switch_can_explicitly_clear_checkbox(self):
        class FakeTab:
            def __init__(self):
                self.calls = []

            def run_js(self, *args):
                self.calls.append(args)
                return {"checked": False}

        bot = AsiBot.__new__(AsiBot)
        bot.tab = FakeTab()
        controls = default_upload_controls()
        controls["modules"]["imprint"]["values"]["unimprinted"] = False

        with patch.object(asi_bot, "click_tab"), patch.object(asi_bot.time, "sleep"):
            bot.fill_imprint({}, upload_controls=controls)

        self.assertEqual(len(bot.tab.calls), 1)
        self.assertIs(bot.tab.calls[0][1], False)

    def test_product_flags_control_unimprinted_checkbox(self):
        class FakeTab:
            def __init__(self):
                self.calls = []

            def run_js(self, *args):
                self.calls.append(args)
                return {"checked": False}

        bot = AsiBot.__new__(AsiBot)
        bot.tab = FakeTab()
        flags = default_product_flags()
        flags["unimprinted"] = False

        with patch.object(asi_bot, "click_tab"), patch.object(asi_bot.time, "sleep"):
            bot.fill_imprint({}, product_flags=flags)

        self.assertEqual(len(bot.tab.calls), 1)
        self.assertIs(bot.tab.calls[0][1], False)

    def test_pricing_switch_overrides_order_less_than_minimum_default(self):
        bot = AsiBot.__new__(AsiBot)
        bot.tab = Mock()
        controls = default_upload_controls()
        controls["modules"]["pricing"]["values"]["order_less_than_minimum"] = False

        with patch.object(asi_bot, "click_tab"), patch.object(asi_bot.time, "sleep"):
            bot.fill_pricing(self._row(), upload_controls=controls)

        matching = [
            call.args
            for call in bot.tab.run_js.call_args_list
            if len(call.args) >= 6 and isinstance(call.args[0], str) and "canOrderLessThanMinimum" in call.args[0]
        ]
        self.assertTrue(matching)
        self.assertIs(matching[0][5], False)
        self.assertIs(matching[0][6], True)

    def test_pricing_skip_does_not_modify_order_less_than_minimum(self):
        bot = AsiBot.__new__(AsiBot)
        bot.tab = Mock()
        controls = default_upload_controls()
        controls["modules"]["pricing"]["fields"]["order_less_than_minimum"] = "skip"

        with patch.object(asi_bot, "click_tab"), patch.object(asi_bot.time, "sleep"):
            bot.fill_pricing(self._row(), upload_controls=controls)

        matching = [
            call.args
            for call in bot.tab.run_js.call_args_list
            if len(call.args) >= 7 and isinstance(call.args[0], str) and "canOrderLessThanMinimum" in call.args[0]
        ]
        self.assertTrue(matching)
        self.assertIs(matching[0][6], False)

    def test_run_upload_propagates_upload_controls(self):
        received = []

        class FakeBot:
            def __init__(self, *args, **kwargs):
                pass

            def start_browser(self):
                pass

            def login(self):
                return True

            def process_product(
                self,
                row,
                image_root_path=None,
                make_active=True,
                distributor_only_view=False,
                upload_controls=None,
            ):
                received.append(upload_controls)
                return "success", ""

            def prepare_for_next_product(self):
                pass

            def close(self):
                pass

        controls = default_upload_controls()
        controls["modules"]["media"]["enabled"] = False
        frame = pd.DataFrame([{"Product_Number": "P100"}])

        with patch.object(asi_bot, "read_excel", return_value=frame), \
             patch.object(asi_bot, "write_result_to_excel"), \
             patch.object(asi_bot, "AsiBot", FakeBot), \
             patch.object(asi_bot.time, "sleep"):
            asi_bot.run_upload(
                "sample.xlsx",
                "12345",
                "user",
                "pass",
                image_root_path="images",
                upload_controls=controls,
            )

        self.assertEqual(received, [controls])


if __name__ == "__main__":
    unittest.main()
