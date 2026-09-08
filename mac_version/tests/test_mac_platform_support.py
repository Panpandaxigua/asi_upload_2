# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import asi_bot
import asi_ui
from license_manager import LicenseManager


class MacPlatformSupportTests(unittest.TestCase):
    def test_license_storage_uses_macos_application_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "tester"

            with patch("license_manager.sys.platform", "darwin"), \
                 patch("license_manager.Path.home", return_value=fake_home):
                manager = LicenseManager()

            self.assertEqual(
                manager.storage_base,
                fake_home / "Library" / "Application Support",
            )
            self.assertIn("Library", str(manager.license_path))

    def test_macos_hardware_info_uses_ioplatform_uuid(self):
        output = '    "IOPlatformUUID" = "ABCDEF12-3456-7890-ABCD-EF1234567890"\n'
        completed = Mock(stdout=output)

        with patch("license_manager.sys.platform", "darwin"), \
             patch("license_manager.subprocess.run", return_value=completed):
            manager = LicenseManager(storage_base=tempfile.gettempdir())
            hardware_info = manager._get_stable_hardware_info()

        self.assertIn("PLATFORM:macOS", hardware_info)
        self.assertIn("IOPLATFORMUUID:ABCDEF12-3456-7890-ABCD-EF1234567890", hardware_info)

    def test_macos_hardware_code_is_stable_across_hostname_machine_and_os_changes(self):
        """改名、切换 CPU 架构或升级 macOS 都不能让同一台 Mac 的硬件码变化。"""
        uuid_line = '    "IOPlatformUUID" = "ABCDEF12-3456-7890-ABCD-EF1234567890"\n'
        scenarios = [
            ("Renamed-MacBook.local", "arm64", "14.5.0"),
            ("Renamed-MacBook-2.local", "arm64", "15.2.0"),
            ("Any-Machine.local", "x86_64", "26.0.0"),
        ]
        codes = []
        for hostname, machine, mac_version in scenarios:
            completed = Mock(stdout=uuid_line)
            with patch("license_manager.sys.platform", "darwin"), \
                 patch("license_manager.subprocess.run", return_value=completed), \
                 patch("socket.gethostname", return_value=hostname), \
                 patch("platform.machine", return_value=machine), \
                 patch("platform.mac_ver", return_value=(mac_version, "", "")):
                manager = LicenseManager(storage_base=tempfile.gettempdir())
                codes.append(manager.generate_hardware_code())

        self.assertTrue(codes[0])
        self.assertEqual(len(set(codes)), 1)

    def test_ui_detects_standard_macos_chrome_path(self):
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

        def fake_is_file(path):
            return str(path).replace("\\", "/") == chrome_path

        with patch("asi_ui.sys.platform", "darwin"), \
             patch("asi_ui.Path.is_file", fake_is_file):
            self.assertEqual(asi_ui.detect_browser_path(), chrome_path)

    def test_bot_detects_standard_macos_chrome_path(self):
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

        def fake_is_file(path):
            return str(path).replace("\\", "/") == chrome_path

        with patch("asi_bot.sys.platform", "darwin"), \
             patch("asi_bot.Path.is_file", fake_is_file):
            self.assertEqual(asi_bot.AsiBot._detect_browser_path(), chrome_path)

    def test_open_file_uses_macos_open_command(self):
        target = Path(tempfile.gettempdir()) / "sample.xlsx"
        calls = []

        with patch("asi_ui.sys.platform", "darwin"), \
             patch("asi_ui.subprocess.Popen", side_effect=lambda args: calls.append(args)):
            asi_ui.open_path_with_default_app(target)

        self.assertEqual(calls, [["open", str(target)]])


if __name__ == "__main__":
    unittest.main()
