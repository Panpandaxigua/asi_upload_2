# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet

from license_manager import LicenseManager


class FixedHardwareLicenseManager(LicenseManager):
    def generate_hardware_code(self):
        return "TEST-HARDWARE-CODE"


class ActivationRetryLicenseManager(FixedHardwareLicenseManager):
    def _parse_activation_code(self, activation_code: str):
        payload = {
            "product_id": self.config.product_id,
            "hw": self.generate_hardware_code(),
            "license_type": "perpetual",
        }
        return json.dumps(payload), b"signature"

    def _verify_signature(self, payload: str, signature: bytes) -> bool:
        return True


class FlakyLicensePath:
    def __init__(self, path: Path):
        self.path = path
        self.write_attempts = 0

    def exists(self):
        return self.path.exists()

    def write_bytes(self, data: bytes):
        self.write_attempts += 1
        if self.write_attempts == 1:
            raise PermissionError("temporary lock")
        return self.path.write_bytes(data)

    def read_bytes(self):
        return self.path.read_bytes()

    def __fspath__(self):
        return str(self.path)

    def __str__(self):
        return str(self.path)


class LicenseManagerTests(unittest.TestCase):
    def test_trial_license_with_date_only_expiry_is_valid_until_end_of_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            lm = FixedHardwareLicenseManager(storage_base=Path(tmp))
            hardware_code = lm.generate_hardware_code()
            fernet = Fernet(lm._get_encryption_key(hardware_code))
            license_data = {
                "product_id": lm.config.product_id,
                "product_name": lm.config.product_name,
                "hardware_code": hardware_code,
                "activation_date": datetime.now().isoformat(timespec="seconds"),
                "license_type": "trial",
                "expires_at": datetime.now().date().isoformat(),
                "status": "ACTIVE",
                "version": 3,
            }
            lm.license_path.write_bytes(
                fernet.encrypt(json.dumps(license_data, ensure_ascii=False).encode("utf-8"))
            )

            valid, _, info = lm.check_license()

            self.assertTrue(valid)
            self.assertEqual(info["expires_at"], datetime.now().date().isoformat())

    def test_activation_retry_uses_standard_time_sleep_without_name_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            lm = ActivationRetryLicenseManager(storage_base=Path(tmp))
            flaky_path = FlakyLicensePath(lm.license_path)
            lm.license_path = flaky_path

            ok, msg = lm.activate("ignored-by-test")

            self.assertTrue(ok, msg)
            self.assertEqual(flaky_path.write_attempts, 2)


if __name__ == "__main__":
    unittest.main()
