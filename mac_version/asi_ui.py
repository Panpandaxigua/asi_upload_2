# -*- coding: utf-8 -*-
"""
ASI Product Upload - PySide6 client UI.
"""
import base64
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

try:
    import win32crypt
except ImportError:
    win32crypt = None

from license_manager import LicenseManager
from product_flags import (
    PRODUCT_FLAG_SCHEMA,
    default_product_flags,
    normalize_product_flags,
)


APP_TITLE = "ASI 产品上传助手"


def get_app_support_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home())


CONFIG_DIR = get_app_support_dir() / "ASIProductUpload" / "Client"
CONFIG_FILE = CONFIG_DIR / "settings.json"
ASI_FIELD_REGISTRY_FILE = CONFIG_DIR / "asi_optional_fields.json"

PAGE_BG = "#F6F7F9"
SIDEBAR_BG = "#FFFFFF"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#EEF2F6"
BORDER = "#D9E0E8"
TEXT_PRIMARY = "#17202A"
TEXT_SECONDARY = "#64748B"
ACCENT = "#2563EB"
ACCENT_HOVER = "#1D4ED8"
SUCCESS = "#0F9F6E"
WARNING = "#B7791F"
DANGER = "#DC2626"
MUTED = "#8A97A8"
LOG_BG = "#101418"

BROWSER_CANDIDATES = [
    lambda: shutil.which("chrome.exe"),
    lambda: shutil.which("chrome"),
    lambda: shutil.which("msedge.exe"),
    lambda: shutil.which("msedge"),
    lambda: os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
    lambda: os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
    lambda: os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    lambda: os.path.join(os.environ.get("PROGRAMFILES", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
    lambda: os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
    lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" if sys.platform == "darwin" else None,
    lambda: str(Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome") if sys.platform == "darwin" else None,
    lambda: "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" if sys.platform == "darwin" else None,
    lambda: str(Path.home() / "Applications" / "Microsoft Edge.app" / "Contents" / "MacOS" / "Microsoft Edge") if sys.platform == "darwin" else None,
]


def repair_mojibake(text):
    """Repair common UTF-8-as-GBK mojibake before showing logs in the UI."""
    value = str(text)
    replacements = {
        "娆㈣繋浣跨敤": "欢迎使用",
        "浜у搧涓婁紶鍔╂墜": "产品上传助手",
        "鍚姩": "启动",
        "鍏?": "共 ",
        "涓骇鍝?": " 个产品",
        "娴忚鍣ㄨ矾寰勫凡閰嶇疆": "浏览器路径已配置",
        "浣跨敤娴忚鍣?": "使用浏览器:",
        "娴忚鍣ㄥ凡鍚姩": "浏览器已启动",
        "姝ｅ湪鐧诲綍": "正在登录",
        "鐧诲綍鎴愬姛": "登录成功",
        "澶勭悊浜у搧": "处理产品",
        "瀵艰埅鍒颁骇鍝佺鐞嗛y闈":"到产品管理页面到产品管理页面",
        "宸叉屻寮€": "已打开",
        "椤甸潰": "页面",
        "鍩虹": "基础",
        "宸蹭繚瀛?": "已保存:",
        "仙紝": "填充",
        "淇濆瓨": "保存",
        "鎴愬姛": "成功",
        "澶辫触": "失败",
        "闂": "问题",
        "瀹屾垚": "完成",
        "缁撴灉": "结果",
        "寮傚父": "异常",
        "娌℃湁鎵惧埌鍏冪礌": "没有找到元素",
    }
    for bad, good in replacements.items():
        value = value.replace(bad, good)
    markers = ("涓", "鍚", "澶", "濉", "淇", "瀵", "宸", "娆", "鐧", "鍥", "缁", "瑙", "鐢")
    if not any(marker in value for marker in markers):
        return value
    try:
        repaired = value.encode("gbk", errors="strict").decode("utf-8", errors="strict")
    except UnicodeError:
        try:
            repaired = value.encode("gb18030", errors="ignore").decode("utf-8", errors="ignore")
        except UnicodeError:
            return value
    if not repaired:
        return value
    chinese_count = sum("\u4e00" <= char <= "\u9fff" for char in repaired)
    original_chinese_count = sum("\u4e00" <= char <= "\u9fff" for char in value)
    return repaired if chinese_count >= original_chinese_count else value


def detect_browser_path():
    """自动探测本机 Chromium 浏览器路径。"""
    for getter in BROWSER_CANDIDATES:
        try:
            candidate = getter()
        except Exception:
            candidate = None
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return ""


def open_path_with_default_app(path):
    path = Path(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)])


def load_settings():
    data = {
        "asi_id": "",
        "username": "",
        "password": "",
        "excel_path": "",
        "image_root_path": "",
        "chrome_path": "",
        "make_active": True,
        "product_flags": default_product_flags(),
    }
    stored = {}
    if CONFIG_FILE.exists():
        try:
            stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            password = stored.get("password", "")
            protected = stored.get("password_protected", "")
            if protected and win32crypt is not None:
                try:
                    encrypted = base64.b64decode(protected)
                    password = win32crypt.CryptUnprotectData(
                        encrypted, None, None, None, 0
                    )[1].decode("utf-8")
                except Exception:
                    password = ""
            public_settings = {
                key: value
                for key, value in stored.items()
                if key not in {"password", "password_protected"}
            }
            data.update(public_settings)
            data["password"] = password
        except Exception:
            pass
    data["make_active"] = bool(data.get("make_active", True))
    if isinstance(stored.get("product_flags"), dict):
        data["product_flags"] = normalize_product_flags(stored["product_flags"])
    else:
        flags = default_product_flags()
        if isinstance(stored.get("distributor_only_view"), bool):
            flags["distributor_only_view"] = stored["distributor_only_view"]
        legacy_modules = stored.get("upload_controls", {}).get("modules", {})
        if isinstance(legacy_modules, dict):
            prop65 = legacy_modules.get("basic_details", {}).get("values", {}).get("prop65")
            unimprinted = legacy_modules.get("imprint", {}).get("values", {}).get("unimprinted")
            if isinstance(prop65, bool):
                flags["prop65_no_chemicals"] = prop65
            if isinstance(unimprinted, bool):
                flags["unimprinted"] = unimprinted
        data["product_flags"] = flags
    return data


def save_settings(data):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        stored = dict(data)
        password = str(stored.pop("password", "") or "")
        stored.pop("password_protected", None)
        if password and win32crypt is not None:
            encrypted = win32crypt.CryptProtectData(
                password.encode("utf-8"), None, None, None, None, 0
            )
            stored["password_protected"] = base64.b64encode(encrypted).decode("ascii")
        CONFIG_FILE.write_text(
            json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except PermissionError:
        logger.warning("无法保存配置文件，路径无写入权限")


def app_stylesheet():
    return f"""
        QWidget {{
            background-color: {PAGE_BG};
            color: {TEXT_PRIMARY};
            font-family: "Microsoft YaHei", "Segoe UI", Arial;
            font-size: 13px;
        }}
        QFrame#Sidebar {{
            background-color: {SIDEBAR_BG};
            border-right: 1px solid {BORDER};
        }}
        QFrame#Panel {{
            background-color: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 8px;
        }}
        QLabel#SectionTitle {{
            font-size: 16px;
            font-weight: 700;
            color: {TEXT_PRIMARY};
        }}
        QLabel#Muted {{
            color: {TEXT_SECONDARY};
        }}
        QLabel#Badge {{
            color: white;
            border-radius: 10px;
            padding: 4px 10px;
            font-weight: 700;
        }}
        QLineEdit {{
            background-color: white;
            border: 1px solid {BORDER};
            border-radius: 6px;
            padding: 7px 10px;
            min-height: 22px;
        }}
        QLineEdit:focus {{
            border-color: {ACCENT};
        }}
        QLineEdit:read-only {{
            background-color: {SURFACE_ALT};
        }}
        QPushButton {{
            border: 1px solid {BORDER};
            border-radius: 6px;
            padding: 7px 14px;
            background-color: white;
            color: {TEXT_PRIMARY};
            font-weight: 600;
            min-height: 22px;
        }}
        QPushButton:hover {{
            background-color: {SURFACE_ALT};
        }}
        QPushButton:disabled {{
            color: {MUTED};
            background-color: #E9EDF2;
        }}
        QPushButton#Primary {{
            border-color: {ACCENT};
            background-color: {ACCENT};
            color: white;
        }}
        QPushButton#Primary:hover {{
            background-color: {ACCENT_HOVER};
        }}
        QPushButton#Danger {{
            border-color: {DANGER};
            background-color: {DANGER};
            color: white;
        }}
        QPushButton#NavButton {{
            border: none;
            border-radius: 6px;
            background-color: transparent;
            color: {TEXT_PRIMARY};
            text-align: left;
            padding: 9px 12px;
            font-weight: 700;
        }}
        QPushButton#NavButton:hover {{
            background-color: {SURFACE_ALT};
        }}
        QPushButton#NavButtonActive {{
            border: none;
            border-radius: 6px;
            background-color: #EAF1FF;
            color: {ACCENT};
            text-align: left;
            padding: 9px 12px;
            font-weight: 800;
        }}
        QCheckBox {{
            spacing: 8px;
            color: {TEXT_PRIMARY};
            font-weight: 600;
        }}
        QComboBox {{
            background-color: white;
            border: 1px solid {BORDER};
            border-radius: 6px;
            padding: 6px 9px;
            min-height: 22px;
            min-width: 150px;
        }}
        QComboBox:focus {{
            border-color: {ACCENT};
        }}
        QScrollArea {{
            border: none;
        }}
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
        }}
        QProgressBar {{
            background-color: #E5EAF0;
            border: none;
            border-radius: 5px;
            height: 10px;
        }}
        QProgressBar::chunk {{
            background-color: {ACCENT};
            border-radius: 5px;
        }}
        QTextEdit {{
            background-color: {LOG_BG};
            color: #E5E7EB;
            border: 1px solid #242B34;
            border-radius: 8px;
            padding: 10px;
            font-family: Consolas, "Courier New";
            font-size: 12px;
        }}
    """


def set_button_kind(button, kind=None):
    if kind:
        button.setObjectName(kind)
    button.setCursor(Qt.PointingHandCursor)
    button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return button


class ActivationPage(QWidget):
    activation_success = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lm = LicenseManager()
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 32)
        outer.addStretch(1)

        card = QFrame()
        card.setObjectName("Panel")
        card.setMaximumWidth(620)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 24, 28, 24)
        card_layout.setSpacing(14)

        title = QLabel("软件激活")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Microsoft YaHei", 22, QFont.Bold))
        card_layout.addWidget(title)

        subtitle = QLabel("请将硬件码发给管理员获取激活码。微信：ypfxnm")
        subtitle.setObjectName("Muted")
        subtitle.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(subtitle)

        hw_label = QLabel("硬件码")
        hw_label.setObjectName("SectionTitle")
        card_layout.addWidget(hw_label)

        hw_row = QHBoxLayout()
        self.hw_code = self.lm.generate_hardware_code()
        self.hw_entry = QLineEdit(self.hw_code)
        self.hw_entry.setReadOnly(True)
        hw_row.addWidget(self.hw_entry, 1)

        self.copy_btn = set_button_kind(QPushButton("复制"), "Primary")
        self.copy_btn.clicked.connect(self._copy_hw_code)
        hw_row.addWidget(self.copy_btn)
        card_layout.addLayout(hw_row)

        act_label = QLabel("激活码")
        act_label.setObjectName("SectionTitle")
        card_layout.addWidget(act_label)

        self.act_entry = QLineEdit()
        self.act_entry.setPlaceholderText("在这里粘贴管理员提供的激活码")
        card_layout.addWidget(self.act_entry)

        self.msg_label = QLabel("")
        self.msg_label.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.msg_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        activate_btn = set_button_kind(QPushButton("激活"), "Primary")
        activate_btn.clicked.connect(self._do_activate)
        btn_row.addWidget(activate_btn)
        exit_btn = set_button_kind(QPushButton("退出"), "Danger")
        exit_btn.clicked.connect(QApplication.quit)
        btn_row.addWidget(exit_btn)
        btn_row.addStretch(1)
        card_layout.addLayout(btn_row)

        outer.addWidget(card, 0, Qt.AlignCenter)
        outer.addStretch(1)

    def _copy_hw_code(self):
        QApplication.clipboard().setText(self.hw_code)
        self.copy_btn.setText("已复制")
        QTimer.singleShot(1400, lambda: self.copy_btn.setText("复制"))

    def _do_activate(self):
        code = self.act_entry.text().strip()
        if not code:
            self.msg_label.setText("请输入激活码")
            self.msg_label.setStyleSheet(f"color: {WARNING};")
            return
        ok, msg = self.lm.activate(code)
        self.msg_label.setText(msg)
        self.msg_label.setStyleSheet(f"color: {SUCCESS if ok else DANGER};")
        if ok:
            QTimer.singleShot(800, self.activation_success.emit)


class MainPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = load_settings()
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.field_sync_thread = None
        self.log_queue = queue.Queue()
        self.status_queue = queue.Queue()
        self.logger_sink_id = None

        self._build_ui()
        self._install_logger_sink()

        self._drain_timer = QTimer(self)
        self._drain_timer.timeout.connect(self._drain_queues)
        self._drain_timer.start(120)

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(190)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 16, 14, 16)
        side_layout.setSpacing(10)

        # Logo
        logo_label = QLabel()
        if getattr(sys, "frozen", False):
            logo_path = Path(sys._MEIPASS) / "asi-logo-1.jpg"
        else:
            logo_path = Path(__file__).parent / "asi-logo-1.jpg"
        if logo_path.exists():
            logo_pix = QPixmap(str(logo_path))
            logo_pix = logo_pix.scaled(162, 50, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(logo_pix)
        else:
            logo_label.setText("ASI Upload")
            logo_label.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        logo_label.setAlignment(Qt.AlignCenter)
        side_layout.addWidget(logo_label)

        brand_subtitle = QLabel("产品自动上传")
        brand_subtitle.setObjectName("Muted")
        brand_subtitle.setAlignment(Qt.AlignCenter)
        side_layout.addWidget(brand_subtitle)
        side_layout.addSpacing(12)

        self.config_nav_btn = QPushButton("配置")
        self.config_nav_btn.clicked.connect(self._show_config_page)
        side_layout.addWidget(self.config_nav_btn)

        self.controls_nav_btn = QPushButton("产品选项")
        self.controls_nav_btn.clicked.connect(self._show_controls_page)
        side_layout.addWidget(self.controls_nav_btn)

        self.log_nav_btn = QPushButton("日志")
        self.log_nav_btn.clicked.connect(self._show_log_page)
        side_layout.addWidget(self.log_nav_btn)

        side_layout.addStretch(1)
        self.side_status_label = QLabel("就绪")
        self.side_status_label.setObjectName("Muted")
        self.side_status_label.setWordWrap(True)
        side_layout.addWidget(self.side_status_label)

        side_layout.addSpacing(8)
        # --- 广告卡片 ---
        ad_frame = QFrame()
        ad_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {ACCENT};
                border-radius: 8px;
                padding: 10px 8px;
            }}
        """)
        ad_inner = QVBoxLayout(ad_frame)
        ad_inner.setContentsMargins(0, 0, 0, 0)
        ad_inner.setSpacing(2)

        ad_title = QLabel("软件定制开发")
        ad_title.setAlignment(Qt.AlignCenter)
        ad_title.setStyleSheet(f"color: white; font-size: 12px; font-weight: bold; background: transparent;")
        ad_inner.addWidget(ad_title)

        ad_sub = QLabel("联系作者")
        ad_sub.setAlignment(Qt.AlignCenter)
        ad_sub.setStyleSheet(f"color: rgba(255,255,255,0.85); font-size: 12px; font-weight: bold; background: transparent;")
        ad_inner.addWidget(ad_sub)

        ad_desc = QLabel("微信：zpfxnm")
        ad_desc.setAlignment(Qt.AlignCenter)
        ad_desc.setStyleSheet(f"color: rgba(255,255,255,0.9); font-size: 11px; background: transparent;")
        ad_inner.addWidget(ad_desc)

        side_layout.addWidget(ad_frame)

        root.addWidget(sidebar)

        self.content_stack = QStackedWidget()
        root.addWidget(self.content_stack, 1)

        self.config_page = self._build_config_page()
        self.controls_page = self._build_controls_page()
        self.log_page = self._build_log_page()
        self.content_stack.addWidget(self.config_page)
        self.content_stack.addWidget(self.controls_page)
        self.content_stack.addWidget(self.log_page)
        self._show_config_page()
        self._auto_detect_browser(silent=True)

    def _build_config_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("运行配置")
        title.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
        title_col.addWidget(title)
        subtitle = QLabel("从 Excel 读取待处理产品，按 Process 列执行，并写回处理结果。")
        subtitle.setObjectName("Muted")
        title_col.addWidget(subtitle)
        header.addLayout(title_col, 1)
        self.status_badge = QLabel("就绪")
        self.status_badge.setObjectName("Badge")
        self.status_badge.setStyleSheet(f"background-color: {ACCENT};")
        header.addWidget(self.status_badge, 0, Qt.AlignTop)
        layout.addLayout(header)

        stats_grid = QGridLayout()
        stats_grid.setSpacing(10)
        self.status_var_label = self._create_stat_card(stats_grid, 0, 0, "运行状态", "就绪", ACCENT)
        self.license_var_label = self._create_stat_card(stats_grid, 0, 1, "授权状态", "检测中", SUCCESS)
        self.current_item_label = self._create_stat_card(stats_grid, 0, 2, "当前产品", "未开始", SUCCESS)
        self.browser_label = self._create_stat_card(stats_grid, 0, 3, "浏览器", "未检测", WARNING)
        layout.addLayout(stats_grid)

        config_panel = QFrame()
        config_panel.setObjectName("Panel")
        config_layout = QVBoxLayout(config_panel)
        config_layout.setContentsMargins(16, 14, 16, 16)
        config_layout.setSpacing(12)
        config_title = QLabel("账号与文件")
        config_title.setObjectName("SectionTitle")
        config_layout.addWidget(config_title)

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.asi_id_entry = self._add_field(form, 0, 0, "ASI ID", self.settings.get("asi_id", ""))
        self.username_entry = self._add_field(form, 0, 1, "账号", self.settings.get("username", ""))
        self.password_entry = self._add_field(form, 0, 2, "密码", self.settings.get("password", ""), password=True)
        self.toggle_pwd_btn = set_button_kind(QPushButton("显示密码"))
        self.toggle_pwd_btn.clicked.connect(self._toggle_password)
        form.addWidget(self.toggle_pwd_btn, 0, 3, Qt.AlignBottom)
        config_layout.addLayout(form)

        path_grid = QGridLayout()
        path_grid.setHorizontalSpacing(8)
        path_grid.setVerticalSpacing(10)
        self.excel_path_entry = self._add_path_row(
            path_grid, 0, "Excel 文件", self.settings.get("excel_path", ""),
            "选择产品 Excel 文件", self._browse_excel, extra_open=True,
        )
        self.image_root_path_entry = self._add_path_row(
            path_grid, 1, "图片总目录", self.settings.get("image_root_path", ""),
            "选择图片总目录", self._browse_image_root,
        )
        self.chrome_path_entry = self._add_path_row(
            path_grid, 2, "Chrome 路径", self.settings.get("chrome_path", ""),
            "可留空，程序会自动检测 Chrome 或 Edge", self._browse_chrome,
        )
        detect_btn = set_button_kind(QPushButton("自动检测"), "Primary")
        detect_btn.clicked.connect(self._auto_detect_browser)
        path_grid.addWidget(detect_btn, 2, 3)
        config_layout.addLayout(path_grid)

        self.make_active_checkbox = QCheckBox("保存后执行 Make Active")
        self.make_active_checkbox.setChecked(bool(self.settings.get("make_active", True)))
        config_layout.addWidget(self.make_active_checkbox)
        layout.addWidget(config_panel)

        run_panel = QFrame()
        run_panel.setObjectName("Panel")
        run_layout = QVBoxLayout(run_panel)
        run_layout.setContentsMargins(16, 14, 16, 16)
        run_layout.setSpacing(10)

        action_bar = QHBoxLayout()
        self.start_btn = set_button_kind(QPushButton("开始运行"), "Primary")
        self.start_btn.clicked.connect(self._start_task)
        action_bar.addWidget(self.start_btn)
        self.precheck_btn = set_button_kind(QPushButton("预检数据"))
        self.precheck_btn.clicked.connect(self._run_precheck)
        action_bar.addWidget(self.precheck_btn)
        self.stop_btn = set_button_kind(QPushButton("停止运行"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_task)
        action_bar.addWidget(self.stop_btn)
        save_btn = set_button_kind(QPushButton("保存配置"))
        save_btn.clicked.connect(self._save_form_settings)
        action_bar.addWidget(save_btn)
        self.sync_asi_fields_btn = set_button_kind(QPushButton("自动更新选填字段"))
        self.sync_asi_fields_btn.setToolTip("登录 ASI，只读抓取当前字段、选项和页面脚本版本，不保存或提交产品。")
        self.sync_asi_fields_btn.clicked.connect(self._start_asi_field_sync)
        action_bar.addWidget(self.sync_asi_fields_btn)
        action_bar.addStretch(1)
        run_layout.addLayout(action_bar)

        self.field_sync_status_label = QLabel("尚未检查 ASI 字段")
        self.field_sync_status_label.setObjectName("Muted")
        run_layout.addWidget(self.field_sync_status_label)

        self.summary_label = QLabel("总计 0  |  成功 0  |  问题 0  |  失败 0")
        self.summary_label.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        run_layout.addWidget(self.summary_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        run_layout.addWidget(self.progress_bar)

        layout.addWidget(run_panel)
        layout.addStretch(1)
        return page

    def _build_controls_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("产品选项")
        title.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
        title_col.addWidget(title)
        subtitle = QLabel("这里的勾选状态会统一写入每一个上传到 ASI 的产品。Excel 空值仍会自动跳过。")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        title_col.addWidget(subtitle)
        header.addLayout(title_col, 1)
        reset_btn = set_button_kind(QPushButton("恢复默认"))
        reset_btn.clicked.connect(self._reset_product_flags)
        header.addWidget(reset_btn, 0, Qt.AlignTop)
        save_btn = set_button_kind(QPushButton("保存产品选项"), "Primary")
        save_btn.clicked.connect(self._save_form_settings)
        header.addWidget(save_btn, 0, Qt.AlignTop)
        layout.addLayout(header)

        panel = QFrame()
        panel.setObjectName("Panel")
        options_grid = QGridLayout(panel)
        options_grid.setContentsMargins(18, 16, 18, 18)
        options_grid.setHorizontalSpacing(20)
        options_grid.setVerticalSpacing(14)
        stored = normalize_product_flags(self.settings.get("product_flags"))
        self.product_flag_checkboxes = {}
        for row, (key, definition) in enumerate(PRODUCT_FLAG_SCHEMA.items()):
            checkbox = QCheckBox(definition["label"])
            checkbox.setChecked(stored[key])
            checkbox.setToolTip(definition["description"])
            description = QLabel(definition["description"])
            description.setObjectName("Muted")
            description.setWordWrap(True)
            options_grid.addWidget(checkbox, row, 0, Qt.AlignTop)
            options_grid.addWidget(description, row, 1)
            self.product_flag_checkboxes[key] = checkbox
        options_grid.setColumnStretch(1, 1)
        layout.addWidget(panel)
        layout.addStretch(1)
        self._controls_running = False
        return page

    def _build_log_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("运行日志")
        title.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
        title_col.addWidget(title)
        subtitle = QLabel("运行过程、页面提示和失败原因会集中显示在这里。")
        subtitle.setObjectName("Muted")
        title_col.addWidget(subtitle)
        header.addLayout(title_col, 1)

        export_btn = set_button_kind(QPushButton("导出日志"))
        export_btn.clicked.connect(self._export_logs)
        header.addWidget(export_btn, 0, Qt.AlignTop)

        clear_btn = set_button_kind(QPushButton("清空日志"))
        clear_btn.clicked.connect(self._clear_logs)
        header.addWidget(clear_btn, 0, Qt.AlignTop)
        layout.addLayout(header)

        # Progress stats bar
        stats_bar = QHBoxLayout()
        stats_bar.setSpacing(20)
        
        self.log_progress_label = QLabel("进度: 等待中")
        self.log_progress_label.setFont(QFont("Microsoft YaHei", 11))
        stats_bar.addWidget(self.log_progress_label)
        
        self.log_success_label = QLabel("成功: 0")
        self.log_success_label.setFont(QFont("Microsoft YaHei", 11))
        self.log_success_label.setStyleSheet(f"color: {SUCCESS}; font-weight: bold;")
        stats_bar.addWidget(self.log_success_label)
        
        self.log_fail_label = QLabel("失败: 0")
        self.log_fail_label.setFont(QFont("Microsoft YaHei", 11))
        self.log_fail_label.setStyleSheet(f"color: {DANGER}; font-weight: bold;")
        stats_bar.addWidget(self.log_fail_label)
        
        stats_bar.addStretch(1)
        layout.addLayout(stats_bar)


        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(f"""
            QTextEdit {{
                background-color: {SURFACE};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 10px;
                font-family: Consolas, "Courier New";
                font-size: 12px;
            }}
        """)
        self.log_text.setText("欢迎使用 ASI 产品上传助手。\n")
        layout.addWidget(self.log_text, 1)
        return page

    def _set_active_nav(self, active):
        buttons = (("config", self.config_nav_btn), ("controls", self.controls_nav_btn), ("log", self.log_nav_btn))
        for name, button in buttons:
            button.setObjectName("NavButtonActive" if name == active else "NavButton")
            button.style().unpolish(button)
            button.style().polish(button)

    def _show_config_page(self):
        self.content_stack.setCurrentWidget(self.config_page)
        self._set_active_nav("config")

    def _show_controls_page(self):
        self.content_stack.setCurrentWidget(self.controls_page)
        self._set_active_nav("controls")

    def _show_log_page(self):
        self.content_stack.setCurrentWidget(self.log_page)
        self._set_active_nav("log")

    def _create_stat_card(self, grid, row, col, title, value, accent_color):
        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("Muted")
        card_layout.addWidget(title_label)
        value_label = QLabel(value)
        value_label.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        value_label.setStyleSheet(f"color: {accent_color};")
        value_label.setWordWrap(True)
        card_layout.addWidget(value_label)
        grid.addWidget(card, row, col)
        return value_label

    def _add_field(self, grid, row, col, label_text, default_value, password=False):
        box = QVBoxLayout()
        box.setSpacing(4)
        label = QLabel(label_text)
        label.setObjectName("Muted")
        box.addWidget(label)
        entry = QLineEdit(default_value)
        if password:
            entry.setEchoMode(QLineEdit.Password)
        box.addWidget(entry)
        grid.addLayout(box, row, col)
        return entry

    def _add_path_row(self, grid, row, label_text, default_value, placeholder, browse_handler, extra_open=False):
        label = QLabel(label_text)
        label.setObjectName("Muted")
        grid.addWidget(label, row, 0)
        entry = QLineEdit(default_value)
        entry.setPlaceholderText(placeholder)
        grid.addWidget(entry, row, 1)
        browse_btn = set_button_kind(QPushButton("选择"), "Primary")
        browse_btn.clicked.connect(browse_handler)
        grid.addWidget(browse_btn, row, 2)
        if extra_open:
            open_btn = set_button_kind(QPushButton("打开"))
            open_btn.clicked.connect(self._open_excel_file)
            grid.addWidget(open_btn, row, 3)
        return entry

    def _browse_excel(self):
        initial_dir = str(Path(self.excel_path_entry.text()).parent) if self.excel_path_entry.text().strip() else ""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择产品数据 Excel 文件",
            initial_dir,
            "Excel 文件 (*.xlsx *.xls);;所有文件 (*.*)",
        )
        if file_path:
            self.excel_path_entry.setText(file_path)
            logger.info(f"已选择 Excel 文件: {file_path}")
            # Check if file is currently open/locked by another program
            try:
                with open(file_path, "a"):
                    pass
            except PermissionError:
                QMessageBox.warning(
                    self, "文件被占用",
                    f"该 Excel 文件正在被其他程序（如 Excel）占用。\n\n"
                    "请先关闭该文件以允许程序写入处理结果，"
                    "然后再点击【开始处理】。\n\n"
                    f"文件:\n{file_path}"
                )
            except OSError as exc:
                logger.warning(f"文件访问检测失败: {exc}")

    def _browse_image_root(self):
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "选择图片总目录",
            self.image_root_path_entry.text().strip() or "",
        )
        if folder_path:
            self.image_root_path_entry.setText(folder_path)
            logger.info(f"已选择图片总目录: {folder_path}")

    def _browse_chrome(self):
        title = "选择 Chrome 或 Edge"
        file_filter = "浏览器可执行文件 (*)" if sys.platform == "darwin" else "浏览器可执行文件 (chrome.exe msedge.exe);;可执行文件 (*.exe);;所有文件 (*.*)"
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            title,
            "",
            file_filter,
        )
        if file_path:
            self.chrome_path_entry.setText(file_path)
            self.browser_label.setText(Path(file_path).name)
            logger.info(f"已选择浏览器路径: {file_path}")

    def _auto_detect_browser(self, silent=False):
        browser_path = detect_browser_path()
        if browser_path:
            self.chrome_path_entry.setText(browser_path)
            self.browser_label.setText(Path(browser_path).name)
            if not silent:
                QMessageBox.information(self, "检测成功", f"已检测到浏览器路径:\n{browser_path}")
        else:
            self.browser_label.setText("未检测到")
            if not silent:
                QMessageBox.warning(self, "未检测到浏览器", "没有检测到标准安装路径，请手动选择 Chrome 或 Edge 的可执行文件。")

    def _open_excel_file(self):
        excel_path = self.excel_path_entry.text().strip()
        if not excel_path:
            QMessageBox.warning(self, "未选择文件", "请先选择 Excel 文件。")
            return
        path_obj = Path(excel_path)
        if not path_obj.exists():
            QMessageBox.critical(self, "文件不存在", f"文件不存在:\n{excel_path}")
            return
        open_path_with_default_app(path_obj)

    def _toggle_password(self):
        if self.password_entry.echoMode() == QLineEdit.Password:
            self.password_entry.setEchoMode(QLineEdit.Normal)
            self.toggle_pwd_btn.setText("隐藏密码")
        else:
            self.password_entry.setEchoMode(QLineEdit.Password)
            self.toggle_pwd_btn.setText("显示密码")

    def _export_logs(self):
        """Export log content to a .txt file chosen by user."""
        from datetime import datetime
        default_name = f"ASI_upload_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出日志", default_name,
            "Text Files (*.txt);;All Files (*)"
        )
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.log_text.toPlainText())
            logger.info(f"日志已导出: {file_path}")
            QMessageBox.information(self, "导出成功", f"日志已保存到:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"无法保存文件:\n{e}")

    def _clear_logs(self):
        self.log_text.clear()

    def _current_product_flags(self):
        return normalize_product_flags({
            key: checkbox.isChecked()
            for key, checkbox in self.product_flag_checkboxes.items()
        })

    def _reset_product_flags(self):
        for key, checked in default_product_flags().items():
            self.product_flag_checkboxes[key].setChecked(checked)

    def _save_current_settings(self):
        save_settings({
            "asi_id": self.asi_id_entry.text().strip(),
            "username": self.username_entry.text().strip(),
            "password": self.password_entry.text(),
            "excel_path": self.excel_path_entry.text().strip(),
            "image_root_path": self.image_root_path_entry.text().strip(),
            "chrome_path": self.chrome_path_entry.text().strip(),
            "make_active": self.make_active_checkbox.isChecked(),
            "product_flags": self._current_product_flags(),
        })

    def _save_form_settings(self):
        self._save_current_settings()
        QMessageBox.information(self, "保存成功", "当前配置已保存，下次打开会自动带出。")

    def _log_start_failure(self, title, message, level="warning"):
        self._show_log_page()
        text = f"启动失败：{title}。{message}"
        if level == "error":
            logger.error(text)
        else:
            logger.warning(text)

    def _validate_form(self):
        if not self.asi_id_entry.text().strip():
            title = "缺少 ASI ID"
            message = "请输入 ASI ID。"
            self._log_start_failure(title, message)
            QMessageBox.warning(self, title, message)
            return False
        if not self.username_entry.text().strip():
            title = "缺少账号"
            message = "请输入账号。"
            self._log_start_failure(title, message)
            QMessageBox.warning(self, title, message)
            return False
        if not self.password_entry.text():
            title = "缺少密码"
            message = "请输入密码。"
            self._log_start_failure(title, message)
            QMessageBox.warning(self, title, message)
            return False
        excel_path = self.excel_path_entry.text().strip()
        if not excel_path:
            title = "缺少 Excel 文件"
            message = "请先选择 Excel 文件。"
            self._log_start_failure(title, message)
            QMessageBox.warning(self, title, message)
            return False
        if not Path(excel_path).is_file():
            title = "Excel 文件不存在"
            message = f"Excel 文件不存在:\n{excel_path}"
            self._log_start_failure(title, message, level="error")
            QMessageBox.critical(self, title, message)
            return False
        image_root = self.image_root_path_entry.text().strip()
        if not image_root:
            title = "缺少图片目录"
            message = "请选择图片总目录。"
            self._log_start_failure(title, message)
            QMessageBox.warning(self, title, message)
            return False
        if not Path(image_root).is_dir():
            title = "图片目录不存在"
            message = f"图片总目录不存在:\n{image_root}"
            self._log_start_failure(title, message, level="error")
            QMessageBox.critical(self, title, message)
            return False
        return True

    def _validate_excel_data(self, excel_path):
        """Pre-check Excel data using JSON options, write issues to Excel."""
        try:
            from tools import run_precheck
            result = run_precheck(excel_path)
            total = result["total"]
            problems = result["problems"]
            clean = result["clean"]
            if problems == 0:
                logger.info(f"预检 {total} products - all passed")
                return
            self._show_log_page()
            logger.warning(f"预检 {total} total | {problems} problems | {clean} clean")
            for item in result.get("warnings", []):
                pn = item.get("product_number", "?")
                logger.warning(f"  [{pn}] ({len(item.get('issues', []))} issues)")
                for iss in item.get("issues", [])[:5]:
                    logger.warning(f"    - {iss}")
            logger.warning("预检 Issues written to ProcessMessage column. Review and fix before uploading.")
        except Exception as e:
            logger.warning(f"预检 Failed: {e}")

    def _start_asi_field_sync(self):
        """Start a read-only live ASI field discovery in the background."""
        if self.field_sync_thread and self.field_sync_thread.is_alive():
            QMessageBox.information(self, "正在更新", "ASI 选填字段正在抓取，请稍候。")
            return
        if self.worker_thread and self.worker_thread.is_alive():
            QMessageBox.information(self, "任务运行中", "请等待当前上传任务结束后再更新字段。")
            return
        missing = []
        if not self.asi_id_entry.text().strip():
            missing.append("ASI ID")
        if not self.username_entry.text().strip():
            missing.append("账号")
        if not self.password_entry.text():
            missing.append("密码")
        if missing:
            QMessageBox.warning(self, "缺少账号信息", "自动更新需要先填写：" + "、".join(missing))
            return

        self._save_current_settings()
        self.sync_asi_fields_btn.setEnabled(False)
        self.sync_asi_fields_btn.setText("正在读取 ASI…")
        self.field_sync_status_label.setText("正在登录并读取 ASI 页面与 JS 字段定义…")
        logger.info("开始从 ASI 自动更新选填字段（只读，不提交产品）。")
        self.field_sync_thread = threading.Thread(target=self._run_asi_field_sync_worker, daemon=True)
        self.field_sync_thread.start()

    def _run_asi_field_sync_worker(self):
        bot = None
        try:
            from asi_bot import AsiBot
            from asi_field_sync import install_field_registry, install_live_options

            bot = AsiBot(
                asi_id=self.asi_id_entry.text().strip(),
                username=self.username_entry.text().strip(),
                password=self.password_entry.text(),
                browser_path=self.chrome_path_entry.text().strip() or None,
            )
            bot.start_browser()
            if not bot.login():
                raise RuntimeError("ASI 登录失败，请检查账号、密码或登录页面状态。")
            capture = bot.collect_optional_field_registry()
            result = install_field_registry(capture, ASI_FIELD_REGISTRY_FILE)
            catalog = bot.collect_live_option_catalog()
            option_result = install_live_options(
                catalog,
                Path(__file__).parent / "asi_options.json",
                CONFIG_DIR / "asi_options.json",
            )
            result.update(option_result)
            self.status_queue.put({"stage": "field_sync_finished", **result})
        except Exception as exc:
            logger.exception("自动更新 ASI 选填字段失败")
            self.status_queue.put({"stage": "field_sync_error", "message": str(exc)})
        finally:
            if bot is not None:
                bot.close()


    def _run_precheck(self):
        """Standalone pre-check triggered by the UI button."""
        try:
            excel_path = self.excel_path_entry.text().strip()
            if not excel_path:
                QMessageBox.warning(self, "缺少文件", "请先选择 Excel 文件。")
                return
            self._show_log_page()
            logger.info("=" * 40)
            logger.info("开始数据预检...")
            from tools import run_precheck
            result = run_precheck(excel_path)
            total = result["total"]
            problems = result["problems"]
            clean = result["clean"]
            if problems == 0:
                logger.info(f"预检完成 {total} products - all passed!")
                QMessageBox.information(self, "预检通过", f"共 {total} 个产品，全部通过校验！")
            else:
                logger.warning(f"预检完成 {total} total | {problems} problems | {clean} clean")
                for item in result.get("warnings", []):
                    pn = item.get("product_number", "?")
                    logger.warning(f"  [{pn}] ({len(item.get('issues', []))} issues)")
                    for iss in item.get("issues", [])[:10]:
                        logger.warning(f"    - {iss}")
                QMessageBox.warning(
                    self, "预检发现问题",
                    f"共 {total} 个产品，{problems} 个有问题。\n详细信息已写入 Excel ProcessMessage 列，请查看日志。"
                )
        except PermissionError:
            QMessageBox.critical(self, "文件被占用", "Excel 文件被其他程序占用，请关闭 Excel 后重试。")
        except Exception as e:
            logger.error(f"预检异常: {e}")
            QMessageBox.critical(self, "预检失败", f"预检过程出错:\n{e}")

    def _set_running(self, running):
        self._controls_running = running
        self.start_btn.setEnabled(not running)
        self.precheck_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.make_active_checkbox.setEnabled(not running)
        self.sync_asi_fields_btn.setEnabled(not running)
        for checkbox in self.product_flag_checkboxes.values():
            checkbox.setEnabled(not running)

    def _start_task(self):
        if self.field_sync_thread and self.field_sync_thread.is_alive():
            QMessageBox.information(self, "正在更新字段", "请等待 ASI 选填字段更新完成后再开始上传。")
            return
        if self.worker_thread and self.worker_thread.is_alive():
            title = "任务运行中"
            message = "当前已有任务在运行，请等待完成或先停止。"
            self._log_start_failure(title, message)
            QMessageBox.information(self, title, message)
            return
        logger.info("已收到开始运行请求，正在检查配置。")
        if not self._validate_form():
            return

        excel_path = self.excel_path_entry.text().strip()
        try:
            with open(excel_path, "a"):
                pass
        except PermissionError:
            title = "文件被占用"
            message = (
                "Excel 文件正在被其他程序（如 Excel）占用，无法写入处理结果。\n\n"
                "请关闭 Excel 或其他占用该文件的程序后重试。\n\n"
                f"文件:\n{excel_path}"
            )
            self._log_start_failure(title, message, level="error")
            QMessageBox.critical(
                self, title, message
            )
            return
        except OSError as exc:
            title = "文件访问失败"
            message = f"无法访问 Excel 文件:\n{excel_path}\n\n{exc}"
            self._log_start_failure(title, message, level="error")
            QMessageBox.critical(self, title, message)
            return

        # ?? ?? Excel ?? ?????????????????????????????????????
        # 预检 Excel 数据 — 有问题直接 fail 并跳过
        self._validate_excel_data(excel_path)


        self._save_current_settings()
        self._show_log_page()
        self.stop_event.clear()
        self.progress_bar.setValue(0)
        self.status_var_label.setText("准备启动")
        self.status_badge.setText("运行中")
        self.status_badge.setStyleSheet(f"background-color: {ACCENT};")
        self.side_status_label.setText("运行中")
        self.current_item_label.setText("等待浏览器启动")
        self._set_running(True)

        self.worker_thread = threading.Thread(target=self._run_worker, daemon=True)
        self.worker_thread.start()

    def _run_worker(self):
        # 添加文件日志，方便打包后排查问题
        _file_log_id = None
        try:
            _log_path = CONFIG_DIR / "run_debug.log"
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _file_log_id = logger.add(
                str(_log_path),
                format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
                rotation="2 MB",
                retention=3,
                encoding="utf-8",
            )
            logger.info(f"调试日志文件: {_log_path}")
        except Exception:
            pass

        from tools import LicenseExpiredError

        try:
            from asi_bot import run_upload

            run_upload(
                excel_path=self.excel_path_entry.text().strip(),
                asi_id=self.asi_id_entry.text().strip(),
                username=self.username_entry.text().strip(),
                password=self.password_entry.text(),
                image_root_path=self.image_root_path_entry.text().strip() or None,
                browser_path=self.chrome_path_entry.text().strip() or None,
                progress_callback=self.status_queue.put,
                stop_event=self.stop_event,
                make_active=self.make_active_checkbox.isChecked(),
                product_flags=self._current_product_flags(),
            )
        except LicenseExpiredError as exc:
            msg = str(exc)
            logger.error(msg)
            self.status_queue.put({"stage": "expired", "message": msg})
        except Exception as exc:
            logger.exception("运行主流程时发生异常")
            self.status_queue.put({"stage": "error", "message": str(exc)})
        finally:
            if _file_log_id is not None:
                try:
                    logger.remove(_file_log_id)
                except Exception:
                    pass

    def _stop_task(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop_event.set()
            self.status_var_label.setText("停止中")
            self.side_status_label.setText("停止中")
            logger.warning("已发送停止请求，等待当前产品处理完成后结束。")

    def _install_logger_sink(self):
        if self.logger_sink_id is not None:
            return
        self.logger_sink_id = logger.add(
            self._enqueue_log,
            format="{time:HH:mm:ss} | {level:<8} | {message}",
            colorize=False,
        )

    def _enqueue_log(self, message):
        self.log_queue.put(repair_mojibake(message).rstrip())

    def _drain_queues(self):
        while not self.log_queue.empty():
            try:
                self.log_text.append(self.log_queue.get_nowait())
            except Exception:
                break
        while not self.status_queue.empty():
            try:
                self._handle_status(self.status_queue.get_nowait())
            except Exception:
                break

    def _handle_status(self, payload):
        stage = payload.get("stage")
        if stage == "field_sync_finished":
            total = payload.get("total", 0)
            added = payload.get("added", 0)
            removed = payload.get("removed", 0)
            changed = payload.get("changed", 0)
            option_groups = payload.get("updated_groups", 0)
            option_values = payload.get("updated_values", 0)
            message = (
                f"已读取 {total} 个动态字段：新增 {added}，删除 {removed}，变化 {changed}；"
                f"并从 ASI 更新 {option_groups} 类、共 {option_values} 个候选值。"
            )
            self.field_sync_status_label.setText(message)
            self.sync_asi_fields_btn.setText("自动更新选填字段")
            self.sync_asi_fields_btn.setEnabled(True)
            logger.info(message)
            QMessageBox.information(self, "字段更新完成", message)
            return
        if stage == "field_sync_error":
            message = payload.get("message", "发生未知错误。")
            self.field_sync_status_label.setText("更新失败：" + message)
            self.sync_asi_fields_btn.setText("自动更新选填字段")
            self.sync_asi_fields_btn.setEnabled(True)
            QMessageBox.critical(self, "字段更新失败", message)
            return
        total = payload.get("total", 0)
        processed = payload.get("processed", 0)
        success = payload.get("success", 0)
        fail = payload.get("fail", 0)
        problem = payload.get("problem", 0)
        item_num = payload.get("item_num", "")
        duration_text = payload.get("duration_text", "")

        self.summary_label.setText(f"总计 {total}  |  成功 {success}  |  问题 {problem}  |  失败 {fail}")
        self.progress_bar.setValue(int(min(processed / total, 1) * 100) if total else 0)
        if item_num:
            self.current_item_label.setText(item_num)
            self.log_progress_label.setText(f"进度: 第 {processed}/{total} 个  [{item_num}]" if total else f"当前: {item_num}")
        self.log_success_label.setText(f"成功: {success}")
        self.log_fail_label.setText(f"失败: {fail}")
        if stage == "browser_ready":
            browser_path = payload.get("browser_path", "")
            self.browser_label.setText(Path(browser_path).name if browser_path else "自动选择")
        elif stage in {"started", "processing"}:
            self.status_var_label.setText("运行中")
            self.side_status_label.setText(f"运行中 {processed}/{total}" if total else "运行中")
        elif stage == "row_done":
            self.status_var_label.setText("处理下一条")
            self.side_status_label.setText(f"已处理 {processed}/{total}")
        elif stage == "finished":
            has_issue = fail > 0 or problem > 0
            self.status_var_label.setText("有问题" if has_issue else "已完成")
            self.status_badge.setText("有问题" if has_issue else "完成")
            self.status_badge.setStyleSheet(f"background-color: {WARNING if has_issue else SUCCESS};")
            self.side_status_label.setText("运行结束")
            self.current_item_label.setText("本次任务已完成")
            self.progress_bar.setValue(100 if total else 0)
            self._set_running(False)
            message = f"任务已结束。总计 {total} 条，成功 {success} 条，问题 {problem} 条，失败 {fail} 条。"
            if duration_text:
                message = f"{message}\n耗时 {duration_text}。"
            if has_issue:
                QMessageBox.warning(self, "运行结束", message)
            else:
                QMessageBox.information(self, "运行完成", message)
        elif stage == "stopped":
            self.status_var_label.setText("已停止")
            self.status_badge.setText("已停止")
            self.status_badge.setStyleSheet(f"background-color: {MUTED};")
            self.side_status_label.setText("已停止")
            self.current_item_label.setText(f"已处理 {processed}/{total}")
            self._set_running(False)
        elif stage == "error":
            self.status_var_label.setText("运行失败")
            self.status_badge.setText("失败")
            self.status_badge.setStyleSheet(f"background-color: {DANGER};")
            self.side_status_label.setText("运行失败")
            self.current_item_label.setText("发生异常")
            self._set_running(False)
            msg_box = QMessageBox(QMessageBox.Critical, "运行失败", payload.get("message", "发生未知错误。"))
            msg_box.setWindowFlags(msg_box.windowFlags() | Qt.WindowStaysOnTopHint)
            msg_box.exec()
        elif stage == "expired":
            self.status_var_label.setText("授权或页面异常")
            self.status_badge.setText("异常")
            self.status_badge.setStyleSheet(f"background-color: {DANGER};")
            self.side_status_label.setText("运行异常")
            self.current_item_label.setText("请联系维护人员")
            self._set_running(False)
            QMessageBox.critical(self, "运行异常", payload.get("message", "授权或页面结构发生变化。"))

    def cleanup(self):
        if self.logger_sink_id is not None:
            logger.remove(self.logger_sink_id)
            self.logger_sink_id = None
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop_event.set()


class AsiUploadApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(980, 680)
        self.resize(1120, 760)
        self.setStyleSheet(app_stylesheet())

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.main_panel = None
        self.activation_page = None
        self._check_and_show()

    def _check_and_show(self):
        try:
            valid, _, _ = LicenseManager().check_license()
        except Exception:
            valid = False

        if valid:
            self._enter_main_app()
        else:
            self._show_activation_page()

    def _show_activation_page(self):
        self.setMinimumSize(560, 440)
        self.resize(640, 500)
        self.activation_page = ActivationPage()
        self.activation_page.activation_success.connect(self._on_activate_success)
        self.stack.addWidget(self.activation_page)
        self.stack.setCurrentWidget(self.activation_page)

    def _on_activate_success(self):
        if self.activation_page:
            self.stack.removeWidget(self.activation_page)
            self.activation_page.deleteLater()
            self.activation_page = None
        self._enter_main_app()

    def _enter_main_app(self):
        self.setMinimumSize(980, 680)
        self.resize(1120, 760)
        self.main_panel = MainPanel()
        self.stack.addWidget(self.main_panel)
        self.stack.setCurrentWidget(self.main_panel)
        self._update_license_label()

    def _update_license_label(self):
        """Read license info and update the stat card label."""
        try:
            valid, _, info = LicenseManager().check_license()
        except Exception:
            self.main_panel.license_var_label.setText("检测失败")
            self.main_panel.license_var_label.setStyleSheet(f"color: {DANGER};")
            return

        if not valid or not info:
            self.main_panel.license_var_label.setText("未激活")
            self.main_panel.license_var_label.setStyleSheet(f"color: {WARNING};")
            return

        license_type = info.get("license_type", "perpetual")
        if license_type == "perpetual":
            maintenance_until = info.get("maintenance_until")
            if maintenance_until:
                lm = LicenseManager()
                maint_expiry = lm._parse_expiry(maintenance_until)
                if maint_expiry and maint_expiry < datetime.now():
                    display = "永久有效（维护已到期）"
                else:
                    display = f"永久有效（维护至 {maintenance_until}）"
            else:
                display = "永久有效"
            self.main_panel.license_var_label.setText(display)
            self.main_panel.license_var_label.setStyleSheet(f"color: {SUCCESS};")
        elif license_type == "trial":
            expires_at = info.get("expires_at")
            self.main_panel.license_var_label.setText(f"试用中 · {expires_at}")
            self.main_panel.license_var_label.setStyleSheet(f"color: {WARNING};")
        elif license_type == "monthly":
            expires_at = info.get("expires_at")
            self.main_panel.license_var_label.setText(f"有效期至 {expires_at}")
            self.main_panel.license_var_label.setStyleSheet(f"color: {WARNING};")
        else:
            self.main_panel.license_var_label.setText("已激活")
            self.main_panel.license_var_label.setStyleSheet(f"color: {SUCCESS};")

    def closeEvent(self, event):
        if self.main_panel:
            if self.main_panel.worker_thread and self.main_panel.worker_thread.is_alive():
                reply = QMessageBox.question(
                    self,
                    "确认退出",
                    "任务仍在运行中，确定要退出界面吗？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    event.ignore()
                    return
                self.main_panel.stop_event.set()
            self.main_panel.cleanup()
        event.accept()


if __name__ == "__main__":
    app = QApplication([])
    # Icon path: use sys._MEIPASS for PyInstaller, fallback to source dir
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent
    icon_path = base / "favicon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = AsiUploadApp()
    window.show()
    app.exec()
