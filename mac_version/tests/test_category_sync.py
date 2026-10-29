import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from asi_bot import AsiBot
from asi_field_sync import install_live_options
from tools import _eval_in_options_rule


class CategorySyncTests(unittest.TestCase):
    def test_collects_all_categories_and_closes_without_apply(self):
        bot = AsiBot("", "", "")
        bot.tab = Mock()
        bot._navigate_field_scan_tab = Mock()
        bot._run_js_safe = Mock(side_effect=[
            {"state": "opening"},
            {"state": "opening_all"},
            {"state": "ready", "categories": ["PET ITEMS", "Cups-Paper"]},
            {"state": "ready", "categories": ["PET ITEMS", "Cups-Paper"]},
            True,
        ])
        with patch("asi_bot.time.sleep", return_value=None):
            result = bot.collect_live_category_catalog(timeout=2)
        self.assertEqual(result, ["PET ITEMS", "Cups-Paper"])
        bot._navigate_field_scan_tab.assert_called_once_with(
            "#/product/info", "product-info-view")
        self.assertNotIn("applyCategories", bot._run_js_safe.call_args_list[-1].args[0])

    def test_live_category_options_are_saved_to_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "bundled.json"
            target = root / "cached.json"
            bundled.write_text(json.dumps({
                "category": {"label": "Category", "options": []},
                "validation_rules": {},
            }), encoding="utf-8")
            result = install_live_options(
                {"category": ["PET ITEMS", "Cups-Paper"]}, bundled, target)
            saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(saved["category"]["options"], ["PET ITEMS", "Cups-Paper"])
        self.assertEqual(result["option_group_counts"]["category"], 2)

    def test_category_validation_accepts_semicolon_separated_values(self):
        row = {"Category": "PET ITEMS;Cups-Paper"}
        options = {"category": {"options": ["PET ITEMS", "Cups-Paper"]}}
        errors = _eval_in_options_rule(
            row, ["Category"], "category", options, is_multi=True)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
