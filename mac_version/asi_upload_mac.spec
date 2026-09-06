# -*- mode: python ; coding: utf-8 -*-
"""ASI Product Upload - macOS PyInstaller spec.

Build on macOS:
    pyinstaller asi_upload_mac.spec --clean --noconfirm
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

_root = Path(SPECPATH)

_ENTRY = str(_root / "asi_ui.py")

_DATAS = [
    (str(_root / "asi-logo-1.jpg"), "."),
    (str(_root / "favicon.ico"), "."),
    (str(_root / "asi_options.json"), "."),
]

_DRISSIONPAGE_SUBS = collect_submodules("DrissionPage")
_WEBSOCKET_SUBS = collect_submodules("websocket")

_HIDDEN = [
    "multiprocessing",
    "asyncio",
    "concurrent.futures",
    "ssl",
    "sqlite3",
    "email",
    "email.mime",
    "email.mime.text",
    "email.mime.multipart",
    "email.mime.base",
    "email.header",
    "email.utils",
    "email.parser",
    "email.message",
    "email.policy",
    "email.encoders",
    "email.charset",
    "email.errors",
    "email.generator",
    "asi_bot",
    "asi_field_sync",
    "tools",
    "license_manager",
    "product_flags",
    "upload_controls",
    "PySide6",
    "openpyxl",
    "pandas",
    "numpy",
    "DrissionPage",
    *_DRISSIONPAGE_SUBS,
    "websocket",
    *_WEBSOCKET_SUBS,
    "requests",
    "cssselect",
    "lxml",
    "urllib3",
    "loguru",
    "cryptography",
    "cryptography.fernet",
    "cryptography.hazmat.primitives",
    "cryptography.hazmat.primitives.asymmetric",
    "cryptography.hazmat.primitives.hashes",
    "cryptography.hazmat.primitives.kdf",
    "cryptography.hazmat.primitives.kdf.pbkdf2",
    "cryptography.hazmat.primitives.serialization",
    "cryptography.hazmat.backends",
]

_EXCLUDES = [
    "tkinter",
    "_tkinter",
    "matplotlib",
    "scipy",
    "IPython",
    "notebook",
    "jupyterlab",
    "pytest",
    "setuptools",
    "wheel",
    "pydoc",
    "http.server",
    "unittest",
    "platformdirs",
    "wmi",
    "pythoncom",
    "pywintypes",
    "win32api",
    "win32com",
    "win32event",
    "win32process",
    "win32timezone",
]

a = Analysis(
    [_ENTRY],
    pathex=[str(_root)],
    binaries=[],
    datas=_DATAS,
    hiddenimports=_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ASI产品上传助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ASI产品上传助手",
)

app = BUNDLE(
    coll,
    name="ASI产品上传助手.app",
    icon=None,
    bundle_identifier="com.asi.productupload",
    info_plist={
        "NSHighResolutionCapable": "True",
        "LSApplicationCategoryType": "public.app-category.productivity",
    },
)
