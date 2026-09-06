# -*- coding: utf-8 -*-
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from asi_ui import MainPanel


def qapp():
    app = QApplication.instance()
    return app or QApplication([])


class StartFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.app = qapp()
        self.panel = MainPanel()

    def tearDown(self):
        self.panel.cleanup()
        self.panel.deleteLater()

    def test_invalid_excel_path_is_written_to_log_page(self):
        missing_excel = Path.cwd() / "__missing_asi_products__.xlsx"
        self.panel.asi_id_entry.setText("65991")
        self.panel.username_entry.setText("user@example.com")
        self.panel.password_entry.setText("secret")
        self.panel.excel_path_entry.setText(str(missing_excel))
        self.panel.image_root_path_entry.setText(str(Path.cwd()))

        with patch("asi_ui.QMessageBox.critical"):
            self.panel._start_task()

        self.panel._drain_queues()

        self.assertIs(self.panel.content_stack.currentWidget(), self.panel.log_page)
        self.assertIn("启动失败", self.panel.log_text.toPlainText())
        self.assertIn("Excel 文件不存在", self.panel.log_text.toPlainText())

    def test_product_flags_follow_running_state(self):
        self.assertFalse(
            self.panel.product_flag_checkboxes["distributor_only_view"].isChecked()
        )

        self.panel._set_running(True)
        self.assertTrue(all(
            not checkbox.isEnabled()
            for checkbox in self.panel.product_flag_checkboxes.values()
        ))

        self.panel._set_running(False)
        self.assertTrue(all(
            checkbox.isEnabled()
            for checkbox in self.panel.product_flag_checkboxes.values()
        ))


if __name__ == "__main__":
    unittest.main()
