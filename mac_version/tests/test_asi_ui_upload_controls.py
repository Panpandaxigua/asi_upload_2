# -*- coding: utf-8 -*-
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from product_flags import PRODUCT_FLAG_SCHEMA, default_product_flags
from asi_ui import MainPanel, load_settings


def qapp():
    app = QApplication.instance()
    return app or QApplication([])


class UploadControlsUiTests(unittest.TestCase):
    def setUp(self):
        self.app = qapp()
        settings = {
            "asi_id": "",
            "username": "",
            "password": "",
            "excel_path": "",
            "image_root_path": "",
            "chrome_path": "",
            "make_active": True,
            "product_flags": default_product_flags(),
        }
        self.settings_patcher = patch("asi_ui.load_settings", return_value=settings)
        self.settings_patcher.start()
        self.panel = MainPanel()

    def tearDown(self):
        self.panel.cleanup()
        self.panel.deleteLater()
        self.settings_patcher.stop()

    def test_product_options_page_exposes_exactly_the_seven_switches(self):
        self.assertEqual(set(self.panel.product_flag_checkboxes), set(PRODUCT_FLAG_SCHEMA))
        self.assertFalse(hasattr(self.panel, "module_checkboxes"))
        self.assertFalse(hasattr(self.panel, "field_mode_combos"))
        self.assertEqual(self.panel.controls_nav_btn.text(), "产品选项")

    def test_current_flags_and_saved_settings_follow_user_selections(self):
        self.panel.product_flag_checkboxes["new_product"].setChecked(False)
        self.panel.product_flag_checkboxes["seo_product"].setChecked(True)
        self.panel.product_flag_checkboxes["unimprinted"].setChecked(False)

        flags = self.panel._current_product_flags()
        self.assertFalse(flags["new_product"])
        self.assertTrue(flags["seo_product"])
        self.assertFalse(flags["unimprinted"])

        with patch("asi_ui.save_settings") as save_mock:
            self.panel._save_current_settings()
        saved = save_mock.call_args.args[0]
        self.assertEqual(saved["product_flags"], flags)
        self.assertNotIn("upload_controls", saved)
        self.assertNotIn("distributor_only_view", saved)

    def test_precheck_no_longer_receives_field_mode_controls(self):
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as excel_file:
            self.panel.excel_path_entry.setText(excel_file.name)
            with patch("tools.run_precheck", return_value={
                "total": 0,
                "problems": 0,
                "clean": 0,
                "warnings": [],
            }) as precheck_mock, patch("asi_ui.QMessageBox.information"):
                self.panel._run_precheck()

        self.assertNotIn("upload_controls", precheck_mock.call_args.kwargs)

    def test_load_settings_migrates_legacy_runtime_switches(self):
        legacy = {
            "distributor_only_view": True,
            "upload_controls": {
                "modules": {
                    "basic_details": {"values": {"prop65": False}},
                    "imprint": {"values": {"unimprinted": False}},
                }
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "settings.json"
            config_file.write_text(json.dumps(legacy), encoding="utf-8")
            with patch("asi_ui.CONFIG_FILE", config_file):
                settings = load_settings()

        self.assertTrue(settings["product_flags"]["distributor_only_view"])
        self.assertFalse(settings["product_flags"]["prop65_no_chemicals"])
        self.assertFalse(settings["product_flags"]["unimprinted"])

    def test_config_page_exposes_automatic_optional_field_update(self):
        self.assertEqual(self.panel.sync_asi_fields_btn.text(), "自动更新选填字段")
        buttons = [button.text() for button in self.panel.findChildren(type(self.panel.sync_asi_fields_btn))]
        self.assertNotIn("导入预检规则", buttons)
        self.assertIn("尚未检查 ASI 字段", self.panel.field_sync_status_label.text())


if __name__ == "__main__":
    unittest.main()
