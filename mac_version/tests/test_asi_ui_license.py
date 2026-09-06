# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from asi_ui import AsiUploadApp


class ValidLicenseManager:
    def check_license(self):
        return True, "已激活", {"license_type": "perpetual"}


class StartupLicenseTests(unittest.TestCase):
    def test_startup_accepts_three_value_license_check_result(self):
        class DummyWindow:
            entered_main = False
            showed_activation = False

            def _enter_main_app(self):
                self.entered_main = True

            def _show_activation_page(self):
                self.showed_activation = True

        window = DummyWindow()

        with patch("asi_ui.LicenseManager", ValidLicenseManager):
            AsiUploadApp._check_and_show(window)

        self.assertTrue(window.entered_main)
        self.assertFalse(window.showed_activation)


if __name__ == "__main__":
    unittest.main()
