from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as datetime_time
from pathlib import Path
from typing import Any

try:
    import winreg
except ImportError:
    winreg = None
try:
    import wmi
except ImportError:
    wmi = None

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


# ====== 新软件只需要改这里 ======
PRODUCT_ID = "ASI_PRODUCT_UPLOAD"
PRODUCT_NAME = "ASI产品上传"
VENDOR = "ASI_PRODUCT_UPLOAD"
TRIAL_DAYS = 1
# ==============================

PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA00rekH+7EMUq34xGNMgR
zYusaV/D5O/LfjLWTd+9lJDTs6I/UNmbSroAxdWMmmQ7YIBoBDvSDgnaqxEEhMW9
i00SajItkAZTIS1m7c7TKUeoPMg8f3W6u4i+NKj6+WjQKreLUvw0Tr/YMAvjFaJ9
L8z5M81AnJIwyhsdQ6woOt2EIzBmakcH3DU1+hong7TxkXgNvVt90KWHUt/Rpiq3
esR0ecpMhH9RRwAnAIF6wYv8Nwbagx4rafm1l6W45QYWYwR2fZgnmO0NuZPG6BnU
PSc04pcONPUFDJPUpN+p/PwNwXljDK1zMKZf8M7JAUTgMHCEbGkD78O9iWjHcCYC
BQIDAQAB
-----END PUBLIC KEY-----"""
ACTIVATION_CODE_PREFIX = "DDLIC3"


@dataclass(frozen=True)
class ProductLicenseConfig:
    product_id: str
    product_name: str
    vendor: str = "DDExpert"
    trial_days: int = 1
    public_key_pem: str = PUBLIC_KEY_PEM

    def safe_product_id(self) -> str:
        value = self.product_id.strip().lower()
        value = re.sub(r"[^a-z0-9_.-]+", "_", value)
        return value or "default_product"

    def safe_vendor(self) -> str:
        value = self.vendor.strip()
        value = re.sub(r'[<>:"/\\|?*]+', "_", value)
        return value or "DDExpert"


DEFAULT_PRODUCT = ProductLicenseConfig(
    product_id=PRODUCT_ID,
    product_name=PRODUCT_NAME,
    vendor=VENDOR,
    trial_days=TRIAL_DAYS,
)


class LicenseManager:
    """Reusable product-scoped offline license manager.

    The client only stores the public key. Trial licenses are also signed
    activation payloads, so receiving the software does not auto-unlock usage.
    """

    def __init__(
        self,
        config: ProductLicenseConfig | None = None,
        storage_base: str | Path | None = None,
    ):
        self.config = config or DEFAULT_PRODUCT
        self.salt = f"{self.config.safe_vendor()}:{self.config.safe_product_id()}:license:v3".encode("utf-8")
        self.storage_base = Path(storage_base) if storage_base is not None else self._default_storage_base()
        self.license_dir = self._get_license_dir()
        self.license_path = self.license_dir / "license.bin"
        self.fernet = None

    # ---- License Storage ----

    def _default_storage_base(self) -> Path:
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support"
        return Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home())

    def _apply_hidden_attributes(self, target_path: str | Path):
        if os.name != "nt":
            return
        try:
            attrs = 0x02 | 0x04
            ctypes.windll.kernel32.SetFileAttributesW(str(target_path), attrs)
        except Exception:
            pass

    def _ensure_hidden_folder(self, folder_path: str | Path) -> Path:
        folder = Path(folder_path)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            folder = Path.home() / f".{self.config.safe_vendor().lower()}" / "licenses" / self.config.safe_product_id()
            folder.mkdir(parents=True, exist_ok=True)
        self._apply_hidden_attributes(folder)
        return folder

    def _get_license_dir(self) -> Path:
        return self._ensure_hidden_folder(
            self.storage_base
            / self.config.safe_vendor()
            / "Licenses"
            / self.config.safe_product_id()
        )

    # ---- Hardware Info ----

    def _get_macos_ioplatform_uuid(self) -> str:
        try:
            completed = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            return "IOPLATFORMUUID_NOT_FOUND"

        match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', completed.stdout or "")
        if match:
            return match.group(1).strip()
        return "IOPLATFORMUUID_NOT_FOUND"

    def _get_macos_stable_hardware_info(self) -> str:
        io_uuid = self._get_macos_ioplatform_uuid()
        hostname = socket.gethostname() or "HOSTNAME_NOT_FOUND"
        machine = platform.machine() or "MACHINE_NOT_FOUND"
        mac_version = platform.mac_ver()[0] or "MAC_VERSION_NOT_FOUND"
        return (
            f"PRODUCT:{self.config.safe_product_id()}|PLATFORM:macOS|"
            f"IOPLATFORMUUID:{io_uuid}|HOST:{hostname}|MACHINE:{machine}|MACOS:{mac_version}"
        )

    def _get_windows_machine_guid(self):
        if os.name != "nt" or winreg is None:
            return "MACHINE_GUID_NOT_AVAILABLE"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                return str(value).strip()
        except Exception:
            return "MACHINE_GUID_NOT_FOUND"

    def _get_stable_hardware_info(self):
        if sys.platform == "darwin":
            return self._get_macos_stable_hardware_info()

        machine_guid = self._get_windows_machine_guid()
        c = None
        if wmi is not None:
            try:
                c = wmi.WMI()
            except Exception:
                pass

        cpu_id = "CPU_ID_NOT_FOUND"
        mobo_serial = "MOBO_SERIAL_NOT_FOUND"
        system_uuid = "SYSTEM_UUID_NOT_FOUND"
        disk_serials = []

        if c is not None:
            try:
                for cpu in c.Win32_Processor():
                    cpu_id = cpu.ProcessorId.strip() if cpu.ProcessorId else cpu_id
                    break
            except Exception:
                pass
            try:
                for board in c.Win32_BaseBoard():
                    mobo_serial = board.SerialNumber.strip() if board.SerialNumber else mobo_serial
                    break
            except Exception:
                pass
            try:
                system_uuid = c.Win32_ComputerSystemProduct()[0].UUID.strip()
            except Exception:
                pass
            try:
                for disk in c.Win32_DiskDrive():
                    if disk.SerialNumber:
                        disk_serials.append(disk.SerialNumber.strip())
            except Exception:
                pass

        if not disk_serials:
            disk_serials.append("DISK_SERIAL_NOT_FOUND")
        stable_disk_serials = ",".join(sorted(disk_serials))
        return (
            f"PRODUCT:{self.config.safe_product_id()}|MACHINE:{machine_guid}|CPU:{cpu_id}|"
            f"MOBO:{mobo_serial}|UUID:{system_uuid}|DISK:{stable_disk_serials}"
        )

    def generate_hardware_code(self):
        hw_info = self._get_stable_hardware_info()
        hasher = hashlib.sha256()
        hasher.update(self.salt)
        hasher.update(hw_info.encode("utf-8"))
        return hasher.hexdigest()[:24].upper()

    # ---- Encryption Key ----

    def _get_encryption_key(self, hardware_code):
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.salt,
            iterations=100000,
        )
        return base64.urlsafe_b64encode(kdf.derive(hardware_code.encode()))

    # ---- Activation ----

    def _decode_activation_segment(self, value: str) -> bytes:
        text = value.strip()
        text += "=" * (-len(text) % 4)
        try:
            return base64.urlsafe_b64decode(text.encode("ascii"))
        except (binascii.Error, ValueError) as exc:
            raise ValueError("激活码格式错误。") from exc

    def _parse_activation_code(self, activation_code: str) -> tuple[str, bytes]:
        parts = activation_code.strip().split(".")
        if len(parts) != 3 or parts[0] != ACTIVATION_CODE_PREFIX:
            raise ValueError("激活码格式错误。")
        payload_bytes = self._decode_activation_segment(parts[1])
        signature = self._decode_activation_segment(parts[2])
        try:
            payload_text = payload_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("激活码格式错误。") from exc
        return payload_text, signature

    def _verify_signature(self, payload: str, signature: bytes) -> bool:
        try:
            from cryptography.hazmat.primitives.asymmetric import padding
            from cryptography.hazmat.primitives import hashes as h, serialization

            public_key = serialization.load_pem_public_key(
                self.config.public_key_pem.encode(),
                None,
            )
            public_key.verify(
                signature,
                payload.encode("utf-8"),
                padding.PSS(mgf=padding.MGF1(h.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                h.SHA256(),
            )
            return True
        except Exception:
            return False

    def activate(self, activation_code: str):
        hardware_code = self.generate_hardware_code()
        try:
            payload_text, signature = self._parse_activation_code(activation_code)
        except ValueError as exc:
            return False, str(exc)

        if not self._verify_signature(payload_text, signature):
            return False, "激活码无效（签名验证失败），请确认激活码完整正确。"

        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return False, "激活信息格式错误。"

        payload_product = str(payload.get("product_id") or payload.get("app_id") or "").strip()
        if payload_product != self.config.product_id:
            return False, f"激活信息与当前软件不匹配，请确认软件为：{self.config.product_name}。"

        payload_hw = (payload.get("hw") or "").upper()
        if payload_hw != hardware_code.upper():
            return False, "激活码与当前设备不匹配，请确认硬件码。"

        license_type = str(payload.get("license_type") or payload.get("type") or "perpetual").strip().lower()
        if license_type not in {"trial", "monthly", "perpetual"}:
            return False, "授权类型无效。"

        license_data = {
            "product_id": self.config.product_id,
            "product_name": self.config.product_name,
            "hardware_code": hardware_code,
            "activation_date": datetime.now().isoformat(timespec="seconds"),
            "starts_at": payload.get("starts_at"),
            "issued_at": payload.get("issued_at"),
            "license_type": license_type,
            "expires_at": payload.get("expires_at"),
            "maintenance_until": payload.get("maintenance_until") or payload.get("until"),
            "customer": payload.get("customer", ""),
            "status": "ACTIVE",
            "version": 3,
        }

        try:
            self._ensure_hidden_folder(self.license_dir)
            encryption_key = self._get_encryption_key(hardware_code)
            self.fernet = Fernet(encryption_key)
            encrypted_data = self.fernet.encrypt(
                json.dumps(license_data, ensure_ascii=False).encode("utf-8")
            )
            # Clear read-only attribute if file exists
            if self.license_path.exists():
                try:
                    os.chmod(self.license_path, 0o666)
                except Exception:
                    pass
            # Retry with exponential back-off (AV / file lock)
            for attempt in range(10):
                try:
                    self.license_path.write_bytes(encrypted_data)
                    break
                except (PermissionError, OSError):
                    if attempt < 9:
                        time.sleep(0.5 + attempt * 0.3)
                    else:
                        raise
            self._apply_hidden_attributes(self.license_path)
            return True, self._activation_success_message(license_data)
        except (PermissionError, OSError):
            return False, "许可证文件被占用，请关闭其他程序实例或杀毒软件后重试。"
        except Exception as exc:
            return False, f"创建许可证时出错: {exc}"

    def start_trial(self) -> tuple[bool, str]:
        return False, "试用也需要管理员提供试用激活码。"

    # ---- License Check ----

    def _read_license(self) -> dict | None:
        if not self.license_path.exists():
            return None
        hardware_code = self.generate_hardware_code()
        try:
            encryption_key = self._get_encryption_key(hardware_code)
            fernet = Fernet(encryption_key)
            decrypted_data = fernet.decrypt(self.license_path.read_bytes())
            info = json.loads(decrypted_data.decode("utf-8"))
            if info.get("hardware_code") != hardware_code:
                return None
            if info.get("product_id") != self.config.product_id:
                return None
            return info
        except Exception:
            return None

    def _parse_expiry(self, value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            if len(text) == 10:
                return datetime.combine(datetime.fromisoformat(text).date(), datetime_time.max)
            return datetime.fromisoformat(text)
        except Exception:
            return None

    def _activation_success_message(self, info: dict[str, Any]) -> str:
        license_type = info.get("license_type")
        if license_type == "trial":
            return f"试用激活成功！有效期至 {info.get('expires_at')}"
        if license_type == "monthly":
            return f"月付授权激活成功！有效期至 {info.get('expires_at')}"
        return "买断授权激活成功！"

    def check_license(self) -> tuple[bool, str, dict | None]:
        info = self._read_license()
        if not info:
            return False, "未找到许可证，请输入试用码或正式激活码。", None

        if info.get("status") != "ACTIVE":
            return False, "许可证状态异常。", info

        license_type = info.get("license_type", "perpetual")
        expires_at = self._parse_expiry(info.get("expires_at"))

        if license_type == "trial":
            if expires_at is None:
                return False, "试用授权缺少到期时间，请重新激活。", info
            remaining = (expires_at - datetime.now()).total_seconds()
            if remaining <= 0:
                return False, "试用已到期，请联系管理员获取正式激活码。", info
            d = int(remaining / 86400)
            h = int((remaining % 86400) / 3600)
            return True, f"试用中（剩余 {d}天{h}小时）", info

        if license_type == "monthly":
            if expires_at is None:
                return False, "月付授权缺少到期时间，请重新激活。", info
            if expires_at < datetime.now():
                return False, "月付授权已到期，请联系管理员续费。", info
            starts_at = info.get("starts_at") or info.get("issued_at") or "-"
            return True, f"已激活（月付授权，开始 {starts_at}，有效期至 {info.get('expires_at')}）", info

        if license_type == "perpetual":
            maintenance_until = info.get("maintenance_until")
            maint_expiry = self._parse_expiry(maintenance_until)
            if maint_expiry and maint_expiry < datetime.now():
                return True, "已激活（买断授权，维护已到期）", info
            if maintenance_until:
                return True, f"已激活（买断授权，维护期至 {maintenance_until}）", info
            return True, "已激活（买断授权）", info

        return False, "许可证授权类型异常。", info

    def get_hardware_info_summary(self) -> dict:
        code = self.generate_hardware_code()
        return {
            "hardware_code": code,
            "display": f"{code[:8]}-{code[8:16]}-{code[16:24]}",
            "product_id": self.config.product_id,
            "product_name": self.config.product_name,
            "license_path": str(self.license_path),
        }
