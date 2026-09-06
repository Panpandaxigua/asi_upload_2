# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools


class AsiOptionsCacheTests(unittest.TestCase):
    def test_default_loader_prefers_automatically_updated_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cached = Path(temp_dir) / "asi_options.json"
            cached.write_text(json.dumps({"source": "live-cache", "validation_rules": {}}), encoding="utf-8")
            with patch.object(tools, "ASI_OPTIONS_CACHE_FILE", cached):
                self.assertEqual(tools.load_asi_options()["source"], "live-cache")

    def test_explicit_path_does_not_get_replaced_by_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cached = root / "cache.json"
            explicit = root / "explicit.json"
            cached.write_text(json.dumps({"source": "cache"}), encoding="utf-8")
            explicit.write_text(json.dumps({"source": "explicit"}), encoding="utf-8")
            with patch.object(tools, "ASI_OPTIONS_CACHE_FILE", cached):
                self.assertEqual(tools.load_asi_options(explicit)["source"], "explicit")


if __name__ == "__main__":
    unittest.main()
