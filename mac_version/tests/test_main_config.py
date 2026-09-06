import tempfile
import unittest
from pathlib import Path

from main import parse_config


class MainConfigTests(unittest.TestCase):
    def test_distributor_only_view_flag_defaults_off_and_can_be_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp) / "products.xlsx"
            book.write_bytes(b"test")

            default_config = parse_config(["--excel", str(book)])
            enabled_config = parse_config([
                "--excel",
                str(book),
                "--distributor-only-view",
            ])

        self.assertFalse(default_config.distributor_only_view)
        self.assertTrue(enabled_config.distributor_only_view)


if __name__ == "__main__":
    unittest.main()
