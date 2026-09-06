# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import asi_bot
import asi_ui


class ClientSettingsStorageTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "darwin", "DPAPI encryption is Windows-only")
    def test_password_is_dpapi_protected_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "ASIProductUpload" / "Client"
            config_file = config_dir / "settings.json"
            with (
                patch.object(asi_ui, "CONFIG_DIR", config_dir),
                patch.object(asi_ui, "CONFIG_FILE", config_file),
            ):
                asi_ui.save_settings({"username": "customer", "password": "S3cret!"})
                raw = config_file.read_text(encoding="utf-8")
                stored = json.loads(raw)
                loaded = asi_ui.load_settings()

        self.assertNotIn("S3cret!", raw)
        self.assertNotIn("password", stored)
        self.assertTrue(stored.get("password_protected"))
        self.assertEqual(loaded["password"], "S3cret!")

    @unittest.skipIf(sys.platform == "darwin", "DPAPI encryption is Windows-only")
    def test_saving_legacy_plaintext_settings_removes_plaintext_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "ASIProductUpload" / "Client"
            config_file = config_dir / "settings.json"
            config_dir.mkdir(parents=True)
            config_file.write_text(
                json.dumps({"username": "legacy", "password": "old-secret"}),
                encoding="utf-8",
            )
            with (
                patch.object(asi_ui, "CONFIG_DIR", config_dir),
                patch.object(asi_ui, "CONFIG_FILE", config_file),
            ):
                loaded = asi_ui.load_settings()
                asi_ui.save_settings(loaded)
                raw = config_file.read_text(encoding="utf-8")
                stored = json.loads(raw)

        self.assertEqual(loaded["password"], "old-secret")
        self.assertNotIn("password", stored)
        self.assertNotIn("old-secret", raw)


class BrowserStorageTests(unittest.TestCase):
    class FakeOptions:
        def __init__(self):
            self.auto_port_enabled = False
            self.tmp_path = None
            self.download_path = None

        def set_argument(self, _argument):
            return self

        def set_browser_path(self, _path):
            return self

        def auto_port(self):
            self.auto_port_enabled = True
            return self

        def set_tmp_path(self, path):
            self.tmp_path = path
            return self

        def set_download_path(self, path):
            self.download_path = path
            return self

    class FakeBrowser:
        def __init__(self, options):
            self.options = options
            self.latest_tab = object()

    def test_browser_uses_isolated_auto_cleaned_client_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = self.FakeOptions()
            with (
                patch.object(asi_bot, "client_storage_base", lambda: Path(tmp)),
                patch.object(asi_bot, "ChromiumOptions", return_value=options),
                patch.object(asi_bot, "Chromium", self.FakeBrowser),
            ):
                bot = asi_bot.AsiBot("id", "user", "password", browser_path="chrome.exe")
                bot.start_browser()

        expected_root = Path(tmp) / "ASIProductUpload" / "Client" / "Browser"
        self.assertTrue(options.auto_port_enabled)
        self.assertEqual(Path(options.tmp_path), expected_root / "Temp")
        self.assertEqual(Path(options.download_path), expected_root / "Downloads")


if __name__ == "__main__":
    unittest.main()
