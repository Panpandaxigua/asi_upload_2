import unittest
from unittest.mock import patch

import pandas as pd

import asi_bot
import tools
from product_flags import default_product_flags


class RunUploadOptionTests(unittest.TestCase):
    def _run_with_options(self, make_active=None, distributor_only_view=None, product_flags=None):
        calls = []

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
                product_flags=None,
            ):
                calls.append((make_active, distributor_only_view, product_flags))
                return "success", ""

            def prepare_for_next_product(self):
                pass

            def close(self):
                pass

        df = pd.DataFrame([{"Product_Number": "P100"}])

        with patch.object(asi_bot, "read_excel", return_value=df), \
             patch.object(asi_bot, "write_result_to_excel"), \
             patch.object(asi_bot, "AsiBot", FakeBot), \
             patch.object(asi_bot.time, "sleep"):
            kwargs = {}
            if make_active is not None:
                kwargs["make_active"] = make_active
            if distributor_only_view is not None:
                kwargs["distributor_only_view"] = distributor_only_view
            if product_flags is not None:
                kwargs["product_flags"] = product_flags
            asi_bot.run_upload(
                "sample.xlsx",
                "12345",
                "user",
                "pass",
                image_root_path="images",
                **kwargs,
            )
        return calls

    def test_run_upload_defaults_to_make_active(self):
        self.assertEqual(self._run_with_options(), [(True, False, None)])

    def test_run_upload_can_skip_make_active(self):
        self.assertEqual(self._run_with_options(make_active=False), [(False, False, None)])

    def test_run_upload_propagates_distributor_only_view(self):
        self.assertEqual(
            self._run_with_options(make_active=False, distributor_only_view=True),
            [(False, True, None)],
        )

    def test_run_upload_propagates_normalized_product_flags(self):
        expected = default_product_flags()
        expected["new_product"] = False
        expected["seo_product"] = True

        self.assertEqual(
            self._run_with_options(product_flags={"new_product": False, "seo_product": True}),
            [(True, False, expected)],
        )

    def test_basic_product_flags_use_asi_observables_without_changing_date(self):
        calls = []

        class FakeTab:
            def run_js(self, script, *args):
                calls.append((script, args))
                return {"ok": True}

        bot = asi_bot.AsiBot.__new__(asi_bot.AsiBot)
        bot.tab = FakeTab()
        flags = default_product_flags()
        flags.update({
            "distributor_only_view": True,
            "new_product": False,
            "seo_product": True,
            "close_out": True,
            "product_confirmed": False,
        })

        result = bot.set_basic_product_flags(flags)

        self.assertTrue(result["ok"])
        self.assertEqual(calls[0][1], (True, False, True, True, False))
        script = calls[0][0]
        for binding in (
            "distOnlyView",
            "newProductFlag",
            "isProductSEOEnabled",
            "isCloseOut",
            "isPriceConfirmed",
        ):
            self.assertIn(binding, script)
        self.assertNotIn("priceConfirmationDate", script)

    def test_distributor_only_view_uses_asi_observable_and_dom_fallback(self):
        calls = []

        class FakeTab:
            def run_js(self, script, *args):
                calls.append((script, args))
                return {"ok": True, "observable": args[0], "checkbox": args[0]}

        bot = asi_bot.AsiBot.__new__(asi_bot.AsiBot)
        bot.tab = FakeTab()

        result = bot.set_distributor_only_view(True)

        self.assertTrue(result["ok"])
        self.assertEqual(calls[0][1], (True,))
        self.assertIn("distOnlyView", calls[0][0])

    def test_browser_alert_false_is_not_a_make_active_error_message(self):
        class FakeTab:
            def handle_alert(self, accept=True, timeout=1):
                return False

        bot = asi_bot.AsiBot.__new__(asi_bot.AsiBot)
        bot.tab = FakeTab()

        self.assertEqual(bot.handle_any_alert(), "")

    def test_make_active_validation_message_extracts_error_line(self):
        message = (
            "Oops! This product cannot be made 'Active' "
            "The following pages have invalid or missing information. "
            "There are 1 Errors for this product. "
            "1. Pricing: Price Grid Base prices cannot have Z discount code. Grid name: '' "
            "Print this List OK"
        )

        self.assertEqual(
            asi_bot.AsiBot.clean_make_active_validation_message(message),
            "Pricing: Price Grid Base prices cannot have Z discount code. Grid name: ''",
        )

    def test_media_save_can_treat_missing_save_button_as_success(self):
        bot = asi_bot.AsiBot.__new__(asi_bot.AsiBot)
        bot.tab = object()

        with patch.object(asi_bot, "click_save_button", return_value=False):
            self.assertIsNone(bot.save_current_tab("Media", missing_ok=True))

    def test_click_tab_retries_when_page_is_refreshing(self):
        class FakeTab:
            def __init__(self):
                self.calls = 0

            def run_js(self, script):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("页面被刷新，请操作前尝试等待页面刷新或加载完成。")
                return True

        tab = FakeTab()

        with patch.object(tools.time, "sleep"):
            self.assertTrue(tools.click_tab(tab, "#/product/info", retries=2))
        self.assertEqual(tab.calls, 2)

    def test_click_tab_retries_when_tab_links_are_not_ready_yet(self):
        class FakeTab:
            def __init__(self):
                self.calls = 0

            def run_js(self, script):
                self.calls += 1
                return self.calls >= 2

        tab = FakeTab()

        with patch.object(tools.time, "sleep"):
            self.assertTrue(tools.click_tab(tab, "#/product/info", retries=2))
        self.assertEqual(tab.calls, 2)

    def test_wait_product_detail_ready_tolerates_refreshing_page(self):
        class FakeTab:
            def __init__(self):
                self.calls = 0

            def run_js(self, script):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("页面被刷新，请操作前尝试等待页面刷新或加载完成。")
                return {"ready": True, "productId": "123"}

        bot = asi_bot.AsiBot.__new__(asi_bot.AsiBot)
        bot.tab = FakeTab()

        with patch.object(asi_bot.time, "sleep"), patch.object(asi_bot.time, "time", side_effect=[0, 0, 1]):
            self.assertTrue(bot.wait_product_detail_ready(timeout=5, stable_checks=1))
        self.assertEqual(bot.tab.calls, 2)


if __name__ == "__main__":
    unittest.main()
