"""
ASI Product Upload - 核心自动化逻辑
基于 DrissionPage 的浏览器自动化，适配 ASI 平台 (Knockout.js SPA)
"""
import time
import os
import json
import re
import shutil
import sys
from pathlib import Path

import pandas as pd
from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import AlertExistsError, PageDisconnectedError
from loguru import logger

from product_flags import normalize_product_flags
from upload_controls import (
    MODE_SKIP,
    field_mode,
    filter_row_for_module,
    module_should_run,
    normalize_upload_controls,
    runtime_switch_value,
    validate_required_fields,
)
from tools import (
    safe_str, split_multi_value, normalize_punctuation, is_nan,
    fill_input, fill_textarea, fill_select_by_text,
    fill_token_input, fill_field_js,
    click_by_js, click_tab, click_save_button,
    wait_and_handle_alert, read_excel, write_result_to_excel,
    get_product_image_folder, iter_image_files,
    LicenseExpiredError, is_transient_page_refresh_error,
    client_storage_base,
)


ASI_LOGIN_URL = "https://espupdates.asicentral.com/login"
MAX_PRICE_TIERS = 8


class AsiBot:
    """ASI 平台自动化上传机器人"""

    def __init__(self, asi_id, username, password, browser_path=None):
        self.asi_id = asi_id
        self.username = username
        self.password = password
        self.browser_path = browser_path
        self.browser = None
        self.tab = None

    def start_browser(self):
        """启动浏览器"""
        co = ChromiumOptions()
        co.set_argument('--start-maximized')

        storage_root = (
            client_storage_base()
            / "ASIProductUpload" / "Client" / "Browser"
        )
        temp_path = storage_root / "Temp"
        download_path = storage_root / "Downloads"
        temp_path.mkdir(parents=True, exist_ok=True)
        download_path.mkdir(parents=True, exist_ok=True)
        co.set_tmp_path(str(temp_path))
        co.set_download_path(str(download_path))
        co.auto_port()

        detected_path = self.browser_path or self._detect_browser_path()
        if detected_path:
            self._configure_browser_path(co, detected_path)
            logger.info(f"使用浏览器: {detected_path}")
        else:
            logger.info("未指定浏览器路径，将交由 DrissionPage 自行查找。")

        self.browser = Chromium(co)
        self.tab = self.browser.latest_tab
        logger.info("浏览器已启动")

    @staticmethod
    def _detect_browser_path():
        """自动探测本机 Chromium 浏览器路径"""
        candidates = [
            lambda: shutil.which("chrome.exe"),
            lambda: shutil.which("chrome"),
            lambda: shutil.which("msedge.exe"),
            lambda: shutil.which("msedge"),
            lambda: __import__('os').path.join(
                __import__('os').environ.get("PROGRAMFILES", ""),
                "Google", "Chrome", "Application", "chrome.exe",
            ),
            lambda: __import__('os').path.join(
                __import__('os').environ.get("PROGRAMFILES(X86)", ""),
                "Google", "Chrome", "Application", "chrome.exe",
            ),
            lambda: __import__('os').path.join(
                __import__('os').environ.get("LOCALAPPDATA", ""),
                "Google", "Chrome", "Application", "chrome.exe",
            ),
            lambda: __import__('os').path.join(
                __import__('os').environ.get("PROGRAMFILES", ""),
                "Microsoft", "Edge", "Application", "msedge.exe",
            ),
            lambda: __import__('os').path.join(
                __import__('os').environ.get("PROGRAMFILES(X86)", ""),
                "Microsoft", "Edge", "Application", "msedge.exe",
            ),
            lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" if sys.platform == "darwin" else None,
            lambda: str(Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome") if sys.platform == "darwin" else None,
            lambda: "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" if sys.platform == "darwin" else None,
            lambda: str(Path.home() / "Applications" / "Microsoft Edge.app" / "Contents" / "MacOS" / "Microsoft Edge") if sys.platform == "darwin" else None,
        ]
        for getter in candidates:
            try:
                candidate = getter()
            except Exception:
                candidate = None
            if candidate and Path(candidate).is_file():
                return str(candidate)
        return ""

    @staticmethod
    def _configure_browser_path(options, browser_path):
        """兼容不同 DrissionPage 版本的浏览器路径设置接口"""
        if not browser_path:
            return False
        attempts = [
            ("set_browser_path", lambda m: m(browser_path)),
            ("set_browser_exe_path", lambda m: m(browser_path)),
            ("set_paths", lambda m: m(browser_path=browser_path)),
            ("set_paths", lambda m: m(chrome_path=browser_path)),
        ]
        for method_name, runner in attempts:
            method = getattr(options, method_name, None)
            if not callable(method):
                continue
            try:
                runner(method)
                logger.info(f"浏览器路径已配置: {browser_path}")
                return True
            except TypeError:
                continue
            except Exception as exc:
                logger.warning(f"设置浏览器路径失败: {exc}")
                return False
        return False

    def close(self):
        """关闭浏览器"""
        if self.browser:
            try:
                self.browser.quit()
            except Exception:
                pass
            logger.info("浏览器已关闭")

    # =========================
    # 登录
    # =========================

    def login(self):
        """登录 ASI 平台"""
        logger.info("正在登录 ASI 平台...")
        self.tab.get(ASI_LOGIN_URL)
        time.sleep(6)

        # 检查是否已经登录（重定向到 ESP Updates）
        current_url = self.tab.url.lower()
        if "espupdates" in current_url and "login" not in current_url:
            logger.info("检测到已有登录会话，跳过登录")
            return True

        # 等待登录表单加载
        try:
            self.tab.wait.ele_displayed("@id=txtAsiNum", timeout=15)
        except Exception:
            logger.warning("登录表单未在15秒内加载")

        # 填写登录表单
        fill_input(self.tab, "@id=txtAsiNum", self.asi_id)
        time.sleep(0.5)
        fill_input(self.tab, "@id=txtUserName", self.username)
        time.sleep(0.5)
        fill_input(self.tab, "@id=txtPassword", self.password)
        time.sleep(0.5)

        # 点击登录按钮
        clicked = False
        for sel in [
            'css:input[value="Login"]',
            'css:#memberLogin input[type="button"]',
            'xpath=//*[@id="memberLogin"]//input[@value="Login"]',
        ]:
            try:
                btn = self.tab.ele(sel, timeout=3)
                if btn:
                    btn.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            self.tab.run_js("LoginUser()")
            logger.info("  使用 LoginUser() JS 登录")

        time.sleep(8)

        # 验证登录
        current_url = self.tab.url.lower()
        if "espupdates" in current_url and "login" not in current_url:
            logger.info("登录成功")
            return True
        if "dashboard" in current_url:
            logger.info("登录成功")
            return True

        try:
            em = self.tab.ele("@id=loginMsg", timeout=2)
            if em and em.text:
                logger.error(f"登录失败: {em.text}")
        except Exception:
            pass
        logger.error(f"登录后URL异常: {self.tab.url}")
        return False

    def _open_existing_product_for_field_scan(self):
        """Open one existing product in edit mode without saving any changes."""
        logger.info("字段更新：正在只读打开一个现有产品以加载动态字段...")
        attempts = ("#/dashboard", "#/scorecard")
        for route in attempts:
            try:
                self.tab.run_js("window.location.hash = arguments[0];", route)
            except Exception:
                continue
            time.sleep(5)
            clicked = self._run_js_safe(r"""
                function visible(el) {
                    return !!(el && (el.offsetParent || el.getClientRects().length));
                }
                var links = Array.from(document.querySelectorAll('a,button')).filter(visible);
                var candidate = links.find(function(el) {
                    return /redirectToEditProduct/i.test(el.getAttribute('data-bind') || '');
                }) || links.find(function(el) {
                    var binding = el.getAttribute('data-bind') || '';
                    var text = (el.textContent || '').trim();
                    return /editProduct|editClick|productClick/i.test(binding) && !/^Edit this Product$/i.test(text);
                });
                if (!candidate) return false;
                candidate.click();
                return true;
            """)
            if not clicked:
                continue

            # Some product states show a read/edit warning before opening the product.
            for _ in range(12):
                try:
                    confirmed = self._run_js_safe(r"""
                        function visible(el) {
                            return !!(el && (el.offsetParent || el.getClientRects().length));
                        }
                        var buttons = Array.from(document.querySelectorAll(
                            '#editProductWarningDialog button,[class*="validationModal"] button'
                        )).filter(visible);
                        var edit = buttons.find(function(el) {
                            return /Edit this Product/i.test((el.textContent || '').trim()) ||
                                /editProduct/i.test(el.getAttribute('data-bind') || '');
                        });
                        if (edit) { edit.click(); return true; }
                        return false;
                    """)
                    if confirmed:
                        logger.info("字段更新：已确认进入产品编辑页（未保存）。")
                    if "#/product/" in safe_str(self.tab.url):
                        self.wait_product_detail_ready(timeout=30, stable_checks=1)
                        return True
                except Exception:
                    pass
                time.sleep(1)
        raise RuntimeError("没有找到可打开的现有产品，无法完整加载动态选填字段。")

    def _navigate_field_scan_tab(self, route, root_id, timeout=30):
        """Open a product tab and wait for its Knockout controls to render."""
        click_tab(self.tab, route)
        start = time.time()
        last_state = {}
        while time.time() - start < timeout:
            try:
                state = self._run_js_safe(r"""
                    var root = document.getElementById(arguments[0]);
                    if (!root) return {exists:false, controls:0, visible:false};
                    return {
                        exists: true,
                        controls: root.querySelectorAll('input:not([type="hidden"]),select,textarea').length,
                        visible: !!(root.offsetParent || root.getClientRects().length),
                        hash: location.hash
                    };
                """, root_id) or {}
                last_state = state
                if state.get("visible") and int(state.get("controls") or 0) > 0:
                    return state
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError(f"ASI 标签页动态字段未完成渲染: {route} ({last_state})")

    def collect_optional_field_registry(self):
        """Scan every rendered product tab using an existing product, without saving."""
        if not self.tab:
            raise RuntimeError("浏览器尚未启动。")
        self._open_existing_product_for_field_scan()
        routes = (
            ("#/product/info", "product-info-view", "basic_details"),
            ("#/product/attributes", "product-attributes-view", "attributes"),
            ("#/product/imprint", "product-imprint-view", "imprint"),
            ("#/product/pricing", "product-pricing-view", "pricing"),
            ("#/product/media", "product-images-view", "media"),
            ("#/product/sku", "product-sku-view", "sku"),
            ("#/product/availability", "product-availability-view", "availability"),
        )
        fields_by_key = {}
        scripts = set()
        source_url = safe_str(self.tab.url)
        scanned_routes = []
        for index, (route, root_id, module) in enumerate(routes):
            state = self._navigate_field_scan_tab(route, root_id)
            capture = self._capture_optional_fields_from_current_page(include_static=(index == 0))
            module_fields = [field for field in capture.get("fields") or [] if field.get("module") == module]
            if not module_fields:
                raise RuntimeError(f"ASI 标签页已打开但未抓到字段: {route}")
            for field in module_fields:
                fields_by_key[field.get("key")] = field
            scripts.update(capture.get("source_scripts") or [])
            source_url = capture.get("source_url") or source_url
            scanned_routes.append(route)
            logger.info(
                f"字段更新：{route} 已渲染 {state.get('controls', 0)} 个控件，识别 {len(module_fields)} 个字段。"
            )
        return {
            "source_url": source_url,
            "source_scripts": sorted(scripts),
            "scan_mode": "existing_product_rendered_tabs",
            "scanned_routes": scanned_routes,
            "fields": list(fields_by_key.values()),
        }

    @staticmethod
    def _walk_lookup_objects(value):
        if isinstance(value, list):
            for item in value:
                yield from AsiBot._walk_lookup_objects(item)
        elif isinstance(value, dict):
            yield value
            for child in value.values():
                if isinstance(child, (list, dict)):
                    yield from AsiBot._walk_lookup_objects(child)

    @staticmethod
    def _unique_lookup_values(values):
        result, seen = [], set()
        for value in values:
            text = safe_str(value)
            marker = text.casefold()
            if text and marker not in seen:
                seen.add(marker)
                result.append(text)
        return result

    @staticmethod
    def _lookup_root_objects(payload):
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("Items", "items", "Results", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            return [payload]
        return []

    def collect_live_option_catalog(self):
        """Read ASI-controlled candidate values from lookup APIs declared by its JS bundle."""
        endpoints = {
            "product_type": "/api/api/lookup/product_types",
            "product_theme": "/api/api/lookup/themes",
            "country": "/api/api/Lookup/countries",
            "item_packaging": "/api/api/lookup/packaging",
            "product_color": "/api/api/lookup/colors",
            "material": "/api/api/lookup/materials",
            "imprint_method": "/api/api/lookup/imprint_methods",
            "price_code": "/api/api/Lookup/discount_rates?q=s",
            "select_size": "/api/api/Lookup/sizes",
        }
        catalog = {}
        for key, endpoint in endpoints.items():
            try:
                payload = self.api_request("GET", endpoint)
                objects = list(self._walk_lookup_objects(payload))
                root_objects = self._lookup_root_objects(payload)
                if key == "product_type":
                    values = [
                        item.get("DisplayName") or item.get("Description")
                        for item in root_objects
                        if item.get("DisplayName") or item.get("Description")
                    ]
                elif key == "product_theme":
                    values = [item.get("CodeValue") for item in objects if item.get("CodeValue")]
                    if not values:
                        values = [item.get("DisplayName") or item.get("Description") for item in objects]
                elif key == "country":
                    values = [item.get("DisplayName") for item in root_objects if item.get("DisplayName")]
                elif key == "item_packaging":
                    values = [item.get("Value") for item in root_objects if item.get("Value")]
                elif key in {"product_color", "material"}:
                    values = [
                        item.get("CodeValue") or item.get("DisplayName")
                        for item in objects
                        if item.get("CodeValue") or item.get("DisplayName")
                    ]
                elif key == "imprint_method":
                    values = [item.get("CodeValue") for item in root_objects if item.get("CodeValue")]
                elif key in {"price_code", "setup_charge_code"}:
                    values = []
                elif key == "select_size":
                    values = [item.get("DisplayName") for item in root_objects if item.get("DisplayName")]
                else:
                    values = []

                if key == "price_code":
                    aliases = {"P": "A", "Q": "B", "R": "C", "S": "D", "T": "E", "U": "F", "V": "G", "W": "H", "X": "I", "Y": "J", "Z": "K"}
                    values = []
                    for item in root_objects:
                        code = safe_str(item.get("IndustryDiscountCode"))
                        if not code:
                            continue
                        try:
                            percent = int(round(float(item.get("DiscountPercent", 0)) * 100))
                        except (TypeError, ValueError):
                            continue
                        display_code = f"{aliases[code]}/{code}" if code in aliases else code
                        values.append(f"{display_code} {percent}%")
                values = self._unique_lookup_values(values)
                if values:
                    catalog[key] = values
                    logger.info(f"候选值更新：{key} 从 ASI 接口读取 {len(values)} 项。")
            except Exception as exc:
                logger.warning(f"候选值更新：{key} 读取失败，保留原配置: {exc}")
        if "price_code" in catalog:
            catalog["setup_charge_code"] = list(catalog["price_code"])
        return catalog

    def _capture_optional_fields_from_current_page(self, include_static=False):
        """Read field metadata from the currently rendered authenticated ASI DOM.

        This operation is deliberately read-only: it does not open a product, click Save,
        or call any write API.
        """
        if not self.tab:
            raise RuntimeError("浏览器尚未启动。")
        script = r"""
return (function () {
  const includeStatic = !!arguments[0];
  const moduleRoots = {
    'product-info-view': 'basic_details',
    'product-attributes-view': 'attributes',
    'product-imprint-view': 'imprint',
    'product-pricing-view': 'pricing',
    'product-images-view': 'media',
    'product-sku-view': 'sku',
    'product-availability-view': 'availability'
  };
  const fields = [];
  const seen = new Set();
  const clean = value => String(value || '').replace(/\s+/g, ' ').replace(/^\s+|\s+$/g, '');
  const inferModule = (element, hint) => {
    if (hint) return hint;
    for (const id of Object.keys(moduleRoots)) {
      if (element.closest && element.closest('#' + id)) return moduleRoots[id];
    }
    return 'other';
  };
  const inferLabel = (element, root) => {
    let label = '';
    if (element.id && root.querySelector) {
      try {
        const exact = root.querySelector('label[for="' + CSS.escape(element.id) + '"]');
        if (exact) label = clean(exact.textContent);
      } catch (_) {}
    }
    if (!label && element.closest) {
      const wrapped = element.closest('label');
      if (wrapped) label = clean(wrapped.textContent);
    }
    if (!label && element.closest) {
      const row = element.closest('.control-group,.controls-row,.form-group,.checkbox,.radio,td,li');
      if (row) {
        const candidate = row.querySelector('label,strong,legend,.control-label');
        if (candidate) label = clean(candidate.textContent);
      }
    }
    return label || clean(element.getAttribute('aria-label')) || clean(element.placeholder) || clean(element.title);
  };
  const bindingName = dataBind => {
    const match = String(dataBind || '').match(/(?:checked|value|selectedOptions|textInput)\s*:\s*([^,}\s]+)/);
    return match ? match[1].replace(/^\$root\./, '') : '';
  };
  const collect = (root, moduleHint, source) => {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('input,select,textarea').forEach(element => {
      const type = (element.type || element.tagName || 'text').toLowerCase();
      if (['hidden', 'submit', 'button', 'reset'].includes(type)) return;
      const dataBind = clean(element.getAttribute('data-bind'));
      const binding = bindingName(dataBind);
      const module = inferModule(element, moduleHint);
      const label = inferLabel(element, root);
      const identity = clean(element.id) || clean(element.name) || binding || label;
      if (!identity) return;
      const key = module + ':' + identity;
      if (seen.has(key)) return;
      seen.add(key);
      const field = {
        key: key,
        module: module,
        label: label,
        type: type,
        id: clean(element.id),
        name: clean(element.name),
        binding: binding,
        data_bind: dataBind,
        required: !!(element.required || element.getAttribute('aria-required') === 'true'),
        source: source || 'document'
      };
      if (element.tagName === 'SELECT') {
        field.options = Array.from(element.options || []).map(option => clean(option.textContent)).filter(Boolean);
      }
      fields.push(field);
    });
  };
  Object.keys(moduleRoots).forEach(id => collect(document.getElementById(id), moduleRoots[id], 'document#' + id));
  // Knockout removes inactive tab contents from the live DOM. Re-read the authenticated
  // page response so fields from tabs that the user has not opened are still discoverable.
  if (includeStatic) try {
    const pageRequest = new XMLHttpRequest();
    pageRequest.open('GET', location.href.split('#')[0], false);
    pageRequest.send(null);
    if (pageRequest.status >= 200 && pageRequest.status < 300) {
      const rawPage = document.createElement('div');
      rawPage.innerHTML = pageRequest.responseText;
      Object.keys(moduleRoots).forEach(id => collect(rawPage.querySelector('#' + id), moduleRoots[id], 'authenticated-page-source#' + id));
    }
  } catch (_) {}
  if (includeStatic) document.querySelectorAll('script[type="text/html"],template').forEach((template, index) => {
    const holder = document.createElement('div');
    holder.innerHTML = template.innerHTML || '';
    collect(holder, '', 'inline-template-' + index);
  });

  const resources = performance.getEntriesByType('resource').map(entry => entry.name).filter(Boolean);
  const templateUrls = resources.filter(url => /\/Tmpl\/Parts\/.*\.tmpl\.html/i.test(url));
  if (includeStatic) templateUrls.forEach(url => {
    try {
      const request = new XMLHttpRequest();
      request.open('GET', url, false);
      request.send(null);
      if (request.status >= 200 && request.status < 300) {
        const holder = document.createElement('div');
        holder.innerHTML = request.responseText;
        const lower = url.toLowerCase();
        const hint = lower.includes('imprint') ? 'imprint' : lower.includes('pric') ? 'pricing' :
          lower.includes('media') || lower.includes('image') ? 'media' : lower.includes('attribute') ? 'attributes' : '';
        collect(holder, hint, url);
      }
    } catch (_) {}
  });
  return {
    source_url: location.href,
    source_scripts: Array.from(document.scripts).map(item => item.src).filter(src => src && src.indexOf(location.origin) === 0),
    fields: fields
  };
})();
"""
        result = self.tab.run_js(script, bool(include_static))
        if not isinstance(result, dict):
            raise RuntimeError("ASI 字段抓取脚本没有返回有效结果。")
        return result

    # =========================
    # 导航到产品创建
    # =========================

    def navigate_to_add_product(self):
        """从 Dashboard 导航到 Add a New Product Wizard"""
        logger.info("导航到产品管理页面...")
        self.prepare_for_next_product()
        # 确保没有残留的浏览器 alert 阻碍后续操作
        self._dismiss_all_alerts()

        # 点击 Manage Products 标签
        try:
            self.tab.ele("xpath=//*[@id='shellTop-view']/div[3]/div/div[1]/ul/li[3]/a").click()
        except Exception:
            click_by_js(self.tab, "a[data-href='#/dashboard']")
        time.sleep(5)

        # 点击 Add a New Product 按钮
        clicked = False
        selectors = [
            "xpath=//*[@id='importExportButtonGroup']/button[contains(., 'Add a New Product')]",
            "xpath=//button[contains(., 'Add a New Product')]",
            "css:#importExportButtonGroup button:last-child",
        ]
        for selector in selectors:
            try:
                btn = self.tab.ele(selector, timeout=8)
                if btn:
                    btn.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            clicked = self._run_js_safe("""return (function() {
                var buttons = Array.from(document.querySelectorAll('button, a'));
                var btn = buttons.find(function(b) {
                    return /Add a New Product/i.test(b.textContent || '');
                });
                if (btn) { btn.click(); return true; }
                return false;
            })()""")

        # Fallback：如果仍找不到按钮（可能上一个产品失败后页面不在 Dashboard），
        # 强制导航到 Dashboard 再试一次
        if not clicked:
            logger.warning("  未找到 Add a New Product 按钮，尝试强制导航到 Dashboard...")
            try:
                self.tab.run_js("window.location.hash = '#/dashboard';")
            except Exception:
                pass
            time.sleep(6)
            for selector in selectors:
                try:
                    btn = self.tab.ele(selector, timeout=8)
                    if btn:
                        btn.click()
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                clicked = self._run_js_safe("""return (function() {
                    var buttons = Array.from(document.querySelectorAll('button, a'));
                    var btn = buttons.find(function(b) {
                        return /Add a New Product/i.test(b.textContent || '');
                    });
                    if (btn) { btn.click(); return true; }
                    return false;
                })()""")

        if not clicked:
            raise RuntimeError("未找到 Add a New Product 按钮")

        try:
            self.tab.wait.ele_displayed("@id=prodName", timeout=25)
        except Exception:
            logger.warning("Add Product 弹窗未显示，当前URL: {}", self.tab.url)
            raise

        logger.info("已打开 Add a New Product 页面")

    def create_product(self, product_name, product_desc, product_type):
        """Create a new ASI product from the Add Product form."""
        logger.info(f"创建产品: {product_name[:60]}")

        try:
            self.tab.wait.ele_displayed("@id=prodName", timeout=20)
        except Exception:
            raise RuntimeError("Add Product 表单未加载，找不到 prodName")

        form_state = self.tab.run_js("""
            function visible(el) { return !!(el && (el.offsetParent || el.getClientRects().length)); }
            function setValue(el, value) {
                if (!el) return false;
                el.focus();
                el.value = value || '';
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new Event('blur', {bubbles:true}));
                return true;
            }
            var name = document.getElementById('prodName');
            var desc = document.getElementById('prodDesc');
            var form = document.getElementById('addNewProduct');
            if (!visible(name) || !form) {
                return {ok:false, reason:'form_not_visible', url: location.href, bodyText: document.body.innerText.slice(0, 300)};
            }
            setValue(name, arguments[0]);
            setValue(desc, arguments[1]);

            var wanted = String(arguments[2] || '').trim();
            var selected = '';
            if (wanted) {
                var sel = form.querySelector('select');
                if (sel) {
                    var options = Array.from(sel.options || []);
                    var wantedLower = wanted.toLowerCase();
                    var match = options.find(function(opt) {
                        return (opt.textContent || '').trim().toLowerCase() === wantedLower;
                    }) || options.find(function(opt) {
                        var text = (opt.textContent || '').trim().toLowerCase();
                        return text.indexOf(wantedLower) >= 0 || wantedLower.indexOf(text) >= 0;
                    });
                    if (match) {
                        sel.value = match.value;
                        selected = (match.textContent || '').trim();
                        sel.dispatchEvent(new Event('change', {bubbles:true}));
                        sel.dispatchEvent(new Event('blur', {bubbles:true}));
                    }
                }
            }
            return {ok:true, selectedType:selected, nameValue:name.value, descLength:(desc && desc.value || '').length};
        """, product_name, product_desc, product_type)
        if not form_state or not form_state.get("ok"):
            raise RuntimeError(f"Add Product 表单状态异常: {form_state}")
        if product_type and not form_state.get("selectedType"):
            logger.warning(f"  产品类型未匹配: {product_type}")
            return False
        elif form_state.get("selectedType"):
            logger.info(f"  产品类型已选择: {form_state.get('selectedType')}")

        clicked = self.tab.run_js("""
            var form = document.getElementById('addNewProduct');
            var buttons = Array.from((form || document).querySelectorAll('button, input[type="button"], input[type="submit"]'));
            var btn = buttons.find(function(b) {
                var text = (b.textContent || b.value || '').trim();
                return /^Apply$/i.test(text);
            }) || buttons[buttons.length - 1];
            if (btn) { btn.click(); return true; }
            return false;
        """)
        if not clicked:
            raise RuntimeError("未找到 Add Product Apply 按钮")
        logger.info("  已点击 Apply")

        start = time.time()
        while time.time() - start < 40:
            current_url = safe_str(getattr(self.tab, "url", ""))
            if "#/product/info" in current_url:
                break
            try:
                if self.tab.run_js("return !!document.getElementById('product-info-view');"):
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            logger.warning(f"等待产品详情页超时，当前URL: {self.tab.url}")
            self.tab.wait.ele_displayed("@id=product-info-view", timeout=20)
        time.sleep(5)
        logger.info("  产品创建成功，进入详情页")
        return True

    def prepare_for_next_product(self):
        """Close modals/backdrops and return to a state where Add Product is clickable."""
        try:
            self.handle_any_alert()
        except Exception:
            pass
        try:
            result = self.tab.run_js("""
                function visible(el) { return !!(el && (el.offsetParent || el.getClientRects().length)); }
                var actions = [];
                var closeButtons = Array.from(document.querySelectorAll(
                    '[class*="publishSuccessModal"] button.close,' +
                    '[class*="publishSuccessModal"] .btn-primary,' +
                    '[class*="validationModal"] button.close,' +
                    '[class*="submittedModal"] button.close,' +
                    '[class*="submittedModal"] .btn,' +
                    '[class*="submittedForReviewModal"] button.close,' +
                    '.modal.in button.close,' +
                    '.modal.fade.in button.close'
                )).filter(visible);
                closeButtons.forEach(function(btn) {
                    try { btn.click(); actions.push('modal-close'); } catch (e) {}
                });
                document.querySelectorAll('.modal-backdrop').forEach(function(el) {
                    el.parentNode && el.parentNode.removeChild(el);
                    actions.push('backdrop-remove');
                });
                document.body.classList.remove('modal-open');
                return actions;
            """)
            if result:
                logger.info(f"  页面弹窗清理: {result}")
        except Exception as e:
            logger.debug(f"清理页面弹窗失败: {e}")

    # =========================
    # ASI API helpers
    # =========================

    def api_request(self, method, url, data=None, timeout=30):
        """
        Call ASI same-origin APIs from the logged-in browser page.

        Running requests inside the page keeps ASI cookies/session headers intact
        and avoids reimplementing authentication in Python.
        """
        has_payload = data is not None
        payload = json.dumps(data) if has_payload else ""
        script = r"""
            var method = arguments[0];
            var url = arguments[1];
            var payload = arguments[2];
            var hasPayload = arguments[3];
            try {
                var xhr = new XMLHttpRequest();
                xhr.open(method, url, false);
                xhr.setRequestHeader('Accept', 'application/json, text/plain, */*');
                if (hasPayload) {
                    xhr.setRequestHeader('Content-Type', 'application/json; charset=utf-8');
                }
                xhr.send(hasPayload ? payload : null);
                var body = xhr.responseText || '';
                return JSON.stringify({
                    status: xhr.status,
                    ok: xhr.status >= 200 && xhr.status < 300,
                    body: body
                });
            } catch (e) {
                return JSON.stringify({
                    status: 0,
                    ok: false,
                    body: String(e && e.message || e),
                    error: String(e && e.stack || e)
                });
            }
        """
        raw = self.tab.run_js(script, method.upper(), url, payload, has_payload)
        try:
            result = json.loads(raw or "{}")
        except Exception as exc:
            raise RuntimeError(f"ASI API 返回无法解析: {raw!r}") from exc

        if not result.get("ok"):
            raise RuntimeError(
                f"ASI API失败 {method.upper()} {url}: "
                f"{result.get('status')} {safe_str(result.get('body'))[:300]}"
            )

        body = result.get("body") or ""
        if not body:
            return None
        try:
            return json.loads(body)
        except Exception:
            return body

    def handle_any_alert(self, accept=True):
        """Close a browser alert if one is open; return its text when available."""
        try:
            text = self.tab.handle_alert(accept=accept, timeout=1)
            if text is None or isinstance(text, bool):
                return ""
            if text:
                logger.warning(f"  页面弹窗: {text}")
            return safe_str(text)
        except Exception:
            return ""

    def _dismiss_all_alerts(self, max_attempts=3):
        """Repeatedly dismiss browser alerts until none remain."""
        for i in range(max_attempts):
            text = self.handle_any_alert()
            if not text:
                break
            logger.info(f"  清除残留弹窗 ({i+1}): {text[:80]}")
            time.sleep(0.5)

    def _run_js_safe(self, script, *args, **kwargs):
        """Run JS on the tab, first dismissing any alert that would cause AlertExistsError."""
        for attempt in range(3):
            try:
                return self.tab.run_js(script, *args, **kwargs)
            except AlertExistsError:
                logger.warning(f"  run_js 遇到 AlertExistsError，尝试清除弹窗后重试 ({attempt+1}/3)")
                self._dismiss_all_alerts()
                time.sleep(1)
            except PageDisconnectedError:
                raise
        # 最后一次尝试，不再捕获
        return self.tab.run_js(script, *args, **kwargs)

    def wait_product_detail_ready(self, timeout=30, stable_checks=2):
        """Wait until the product detail SPA is readable after ASI finishes refreshing."""
        start = time.time()
        stable = 0
        last_error = ""
        while time.time() - start < timeout:
            try:
                state = self.tab.run_js(r"""
                    var hasInfo = !!document.getElementById('product-info-view');
                    var hasAnyProductView = !!(
                        document.getElementById('product-info-view') ||
                        document.getElementById('product-attributes-view') ||
                        document.getElementById('product-images-view') ||
                        document.getElementById('product-pricing-view')
                    );
                    var productId = '';
                    try {
                        if (window.require) {
                            var cfg = require('config');
                            if (cfg && cfg.currentProductId) {
                                productId = String(typeof cfg.currentProductId === 'function'
                                    ? cfg.currentProductId()
                                    : cfg.currentProductId);
                            }
                        }
                    } catch (e) {}
                    return {ready: hasInfo || hasAnyProductView, productId: productId};
                """) or {}
                if state.get("ready") and safe_str(state.get("productId")):
                    stable += 1
                    if stable >= max(1, int(stable_checks or 1)):
                        return True
                else:
                    stable = 0
            except Exception as exc:
                last_error = safe_str(exc)
                stable = 0
                if not is_transient_page_refresh_error(exc):
                    logger.debug(f"等待产品详情页稳定时 JS 读取失败: {exc}")
            time.sleep(1)
        raise RuntimeError(f"产品详情页加载未稳定: {last_error[:120]}")

    @staticmethod
    def clean_make_active_validation_message(message):
        """Extract the actionable ASI validation errors from the Make Active modal."""
        text = re.sub(r"\s+", " ", safe_str(message)).strip()
        if not text:
            return ""

        matches = re.findall(
            r"(?:^|\s)\d+\.\s+(.+?)(?=(?:\s+\d+\.\s+)|\s+Print this List\b|\s+OK\b|\s+Cancel\b|$)",
            text,
            flags=re.IGNORECASE,
        )
        if matches:
            return "; ".join(m.strip(" ;") for m in matches if m.strip())

        text = re.sub(r"^Oops!\s+This product cannot be made 'Active'\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(
            r"^The following pages have invalid or missing information\..*?There are\s+\d+\s+Errors?\s+for this product\.\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\s+Print this List\b.*$", "", text, flags=re.IGNORECASE)
        return text.strip(" ;")

    @staticmethod
    def friendly_error_message(exc, context=""):
        """Extract a user-friendly message from an exception (especially DrissionPage JS errors)."""
        import re
        msg = str(exc)
        # Try extract description from JSON detail block (DrissionPage JS errors)
        m = re.search(r"'description':\s*'([^']+)'", msg)
        if m:
            desc = m.group(1).replace("\\n", " ").replace("'", "")
            short = desc[:200]
            return f"{context}: {short}" if context else short
        # Try extract Message: pattern
        m = re.search(r"Message:\s*([^;\n]+)", msg)
        if m:
            short = m.group(1).strip()[:200]
            return f"{context}: {short}" if context else short
        # Remove JS function source blocks
        clean = re.sub(r"JS:\s*function\(\)\s*\{[\s\S]*?\}\s*,?\s*", "", msg)
        clean = re.sub(r"function\(\)\s*\{[\s\S]{100,}\}", "[JS代码省略]", clean)
        clean = clean.replace("JavaScript运行错误。", "").strip()
        if len(clean) > 200:
            clean = clean[:200] + "..."
        return f"{context}: {clean}" if context else clean

    def get_current_product_id(self):
        """Read ASI current product id from RequireJS config or URL fallback."""
        product_id = self.tab.run_js(r"""
            try {
                if (window.require) {
                    var cfg = require('config');
                    if (cfg && cfg.currentProductId) {
                        var id = (typeof cfg.currentProductId === 'function')
                            ? cfg.currentProductId()
                            : cfg.currentProductId;
                        if (id) return String(id);
                    }
                }
            } catch (e) {}
            try {
                if (window.ko) {
                    var sections = ['product-info-view', 'product-images-view', 'product-pricing-view', 'product-attributes-view'];
                    for (var i = 0; i < sections.length; i++) {
                        var el = document.getElementById(sections[i]);
                        var vm = el ? ko.dataFor(el) : null;
                        if (vm && vm.productInfo) {
                            var pi = (typeof vm.productInfo === 'function') ? vm.productInfo() : vm.productInfo;
                            if (pi && pi.id) {
                                var kid = (typeof pi.id === 'function') ? pi.id() : pi.id;
                                if (kid) return String(kid);
                            }
                        }
                    }
                }
            } catch (e) {}
            var href = String(window.location.href || '');
            var m = href.match(/[?&#](?:product_id|productId|id)=([0-9]+)/i);
            return m ? m[1] : '';
        """)
        product_id = safe_str(product_id)
        if not product_id:
            raise RuntimeError("无法获取当前 ASI Product ID")
        return product_id

    def get_product_json(self, product_id=None):
        product_id = safe_str(product_id) or self.get_current_product_id()
        return self.api_request("GET", f"/api/api/Product/{product_id}")

    def save_product_json(self, product_json):
        return self.api_request("POST", "/api/api/Product/", product_json)

    def get_product_media_json(self, product_id=None):
        product_id = safe_str(product_id) or self.get_current_product_id()
        return self.api_request("GET", f"/api/api/ProductMedia/?product_id={product_id}")

    def get_product_media_count(self, product_id=None):
        try:
            media = self.get_product_media_json(product_id)
            if isinstance(media, dict):
                items = media.get("Items") or media.get("items") or []
                return len(items)
        except Exception as e:
            logger.debug(f"读取媒体数量失败: {e}")
        return None

    def apply_basic_product_json(self, row, product_json):
        """Apply simple Excel fields to the Product DTO returned by ASI."""
        payload = self.build_basic_details_payload(row)
        field_map = {
            "productNumber": "AsiProdNo",
            "productName": "Name",
            "description": "Description",
            "summary": "Summary",
        }
        for payload_key, api_key in field_map.items():
            value = safe_str(payload.get(payload_key, ""))
            if value:
                product_json[api_key] = value

        additional_shipping = safe_str(row.get("Additional Shipping Information", ""))
        if additional_shipping:
            product_json["AddtionalShippingInfo"] = additional_shipping

        return product_json

    @staticmethod
    def row_value(row, *names):
        normalized_lookup = {}
        if hasattr(row, "index"):
            keys = list(row.index)
        elif hasattr(row, "keys"):
            keys = list(row.keys())
        else:
            keys = []
        for key in keys:
            normalized = re.sub(r"[^a-z0-9]+", "", safe_str(key).lower())
            if normalized and normalized not in normalized_lookup:
                normalized_lookup[normalized] = key
        for name in names:
            value = safe_str(row.get(name, ""))
            if value:
                return value
            normalized_name = re.sub(r"[^a-z0-9]+", "", safe_str(name).lower())
            matched_key = normalized_lookup.get(normalized_name)
            if matched_key is not None:
                value = safe_str(row.get(matched_key, ""))
                if value:
                    return value
        return ""

    @staticmethod
    def build_basic_details_payload(row, include_prop65=True):
        return {
            "productNumber": AsiBot.row_value(row, "Product_Number", "Product Number"),
            "productName": AsiBot.row_value(row, "Product_Name", "Product Name"),
            "description": AsiBot.row_value(row, "Description", "Product Description"),
            "summary": AsiBot.row_value(row, "Summary", "Summary Description", "Product Summary"),
            "prop65NoChemicals": bool(include_prop65),
        }

    @staticmethod
    def split_value_unit(value):
        """Split Excel values like '30 Case', '30 in', or '30 LBS'."""
        raw = safe_str(value).strip()
        if not raw:
            return "", ""
        match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?(?:\s+\d+/\d+)?|\d+/\d+)\s*([A-Za-z]+)?", raw)
        if not match:
            return raw, ""
        return match.group(1).strip(), (match.group(2) or "").strip()

    @staticmethod
    def shipping_unit_code(unit):
        key = safe_str(unit).strip().lower()
        codes = {
            "in": "INCH",
            "inch": "INCH",
            "inches": "INCH",
            "ft": "FEET",
            "feet": "FEET",
            "foot": "FEET",
            "cm": "CENT",
            "centimeter": "CENT",
            "centimeters": "CENT",
            "mm": "MILL",
            "millimeter": "MILL",
            "millimeters": "MILL",
            "m": "METR",
            "meter": "METR",
            "meters": "METR",
            "yd": "YARD",
            "yds": "YARD",
            "yard": "YARD",
            "yards": "YARD",
        }
        return codes.get(key, "")

    @staticmethod
    def weight_unit_code(unit):
        key = safe_str(unit).strip().lower()
        codes = {
            "lb": "POUN",
            "lbs": "POUN",
            "pound": "POUN",
            "pounds": "POUN",
            "oz": "OUNC",
            "ounce": "OUNC",
            "ounces": "OUNC",
            "kg": "KIGM",
            "kgs": "KIGM",
            "kilogram": "KIGM",
            "kilograms": "KIGM",
            "g": "GRAM",
            "gram": "GRAM",
            "grams": "GRAM",
        }
        return codes.get(key, "")

    @staticmethod
    def item_uom_code(unit):
        key = safe_str(unit).strip().lower()
        codes = {
            "box": "BOXE",
            "boxes": "BOXE",
            "carton": "CART",
            "cartons": "CART",
            "case": "CASE",
            "cases": "CASE",
            "other": "OTHE",
        }
        return codes.get(key, "")

    @staticmethod
    def build_shipping_payload(row):
        num_items, item_uom = AsiBot.split_value_unit(row.get("Number of Items", ""))
        length, length_uom = AsiBot.split_value_unit(row.get("Shipping Dimensions L", ""))
        width, width_uom = AsiBot.split_value_unit(row.get("Shipping Dimensions W", ""))
        height, height_uom = AsiBot.split_value_unit(row.get("Shipping Dimensions H", ""))
        weight, weight_uom = AsiBot.split_value_unit(row.get("Shipping Weight ", row.get("Shipping Weight", "")))

        return {
            "numItems": num_items,
            "numItemUOM": AsiBot.item_uom_code(item_uom),
            "dim1Value": length,
            "dim1UOM": AsiBot.shipping_unit_code(length_uom),
            "dim2Value": width,
            "dim2UOM": AsiBot.shipping_unit_code(width_uom),
            "dim3Value": height,
            "dim3UOM": AsiBot.shipping_unit_code(height_uom),
            "shipWeight": weight,
            "shipUOM": AsiBot.weight_unit_code(weight_uom),
            "shipperBillsBy": safe_str(row.get("Shipper Bills by", "")).strip(),
            "packaging": split_multi_value(row.get("Item Packaging", ""), seps=(",",)),
            "additionalShippingInfo": safe_str(row.get("Additional Shipping Information", "")),
        }

    @staticmethod
    def build_origin_payload(row):
        """Build Basic Details country/origin payload from Excel values."""
        return {
            "manufacturedIn": safe_str(row.get("Manufactured In", "")),
            "assembledIn": safe_str(row.get("Assembled In", "")),
            "decoratedIn": safe_str(row.get("Decorated In", "")),
        }

    @staticmethod
    def normalize_size_label(value):
        text = normalize_punctuation(value).lower()
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def size_type_code(select_size):
        key = AsiBot.normalize_size_label(select_size)
        code_map = {
            "none": "",
            "dimension(l,w,h)": "DIMS",
            "dimension (l,w,h)": "DIMS",
            "dims": "DIMS",
            "capacity (gb, mb)": "CAPS",
            "capacity(gb, mb)": "CAPS",
            "caps": "CAPS",
            "volume (oz,lb)": "SVWT",
            "volume(oz,lb)": "SVWT",
            "svwt": "SVWT",
            "other - (one size, custom)": "SOTH",
            "other - (one size, custom)": "SOTH",
            "soth": "SOTH",
            "apparel-standard & numbered - (s,m,l,10,12)": "SSNM",
            "apparel-standard & numbered - (s, m, l, 10, 12)": "SSNM",
            "ssnm": "SSNM",
            "apparel-mens/tall": "MENS",
            "mens": "MENS",
            "apparel-mens/tall (tall)": "MENT",
            "apparel-ment/tall": "MENT",
            "ment": "MENT",
            "apparel-unisex": "UNIS",
            "unis": "UNIS",
            "apparel-womens/tall": "WOMS",
            "woms": "WOMS",
            "apparel-womens/tall (tall)": "WOMT",
            "apparel-womt/tall": "WOMT",
            "womt": "WOMT",
            "apparel-boys": "BOYS",
            "boys": "BOYS",
            "apparel-girls": "GIRL",
            "girl": "GIRL",
            "apparel-youth": "YOUT",
            "yout": "YOUT",
            "apparel - bra (32c)": "SABR",
            "apparel-bra (32c)": "SABR",
            "sabr": "SABR",
            "apparel - hosiery/uniform (a, ab)": "SAHU",
            "apparel-hosiery/uniform (a, ab)": "SAHU",
            "sahu": "SAHU",
            "apparel - infant/toddler (3 months, 2t)": "SAIT",
            "apparel-infant/toddler (3 months, 2t)": "SAIT",
            "sait": "SAIT",
            "apparel - dress shirts (15-15 1/2)": "SANS",
            "apparel-dress shirts (15-15 1/2)": "SANS",
            "sans": "SANS",
            "apparel - pants (32-32)": "SAWI",
            "apparel-pants (32-32)": "SAWI",
            "sawi": "SAWI",
        }
        upper = safe_str(select_size).strip().upper()
        if upper in {"DIMS", "CAPS", "SVWT", "SOTH", "SSNM", "MENS", "MENT", "UNIS", "WOMS", "WOMT", "BOYS", "GIRL", "YOUT", "SABR", "SAHU", "SAIT", "SANS", "SAWI"}:
            return upper
        return code_map.get(key, "")

    @staticmethod
    def size_dimension_name(name):
        key = AsiBot.normalize_size_label(name).rstrip(".")
        aliases = {
            "l": "Length",
            "len": "Length",
            "length": "Length",
            "w": "Width",
            "width": "Width",
            "h": "Height",
            "height": "Height",
            "d": "Depth",
            "depth": "Depth",
            "dia": "Dia",
            "diameter": "Dia",
            "thickness": "Thickness",
            "t": "Thickness",
            "arc": "Arc",
            "area": "Area",
            "circumference": "Circumference",
        }
        return aliases.get(key, safe_str(name))

    @staticmethod
    def dimension_unit_code(unit):
        key = safe_str(unit).lower().replace(".", "")
        extra = {
            '"': "INCH",
            "sqft": "SQFT",
            "sq ft": "SQFT",
            "square feet": "SQFT",
            "sqin": "SQUA",
            "sq in": "SQUA",
            "square inch": "SQUA",
            "square inches": "SQUA",
            "mil": "METE",
        }
        return extra.get(key, AsiBot.shipping_unit_code(unit))

    @staticmethod
    def size_measure_unit_code(unit):
        key = safe_str(unit).lower().replace(".", "")
        codes = {
            "gb": "GIGA",
            "gigabyte": "GIGA",
            "gigabytes": "GIGA",
            "kb": "KILO",
            "kilobyte": "KILO",
            "kilobytes": "KILO",
            "mb": "MEGB",
            "megabyte": "MEGB",
            "megabytes": "MEGB",
            "megapixel": "MEGA",
            "megapixels": "MEGA",
            "tb": "TERA",
            "terabyte": "TERA",
            "terabytes": "TERA",
            "gallon": "GALL",
            "gallons": "GALL",
            "g": "GRAM",
            "gram": "GRAM",
            "grams": "GRAM",
            "kg": "KIGM",
            "kilogram": "KIGM",
            "kilograms": "KIGM",
            "lb": "POUN",
            "lbs": "POUN",
            "pound": "POUN",
            "pounds": "POUN",
            "l": "LITE",
            "liter": "LITE",
            "liters": "LITE",
            "mg": "MIGR",
            "milligram": "MIGR",
            "milligrams": "MIGR",
            "ml": "MMLL",
            "milliliter": "MMLL",
            "milliliters": "MMLL",
            "oz": "OUNC",
            "ounce": "OUNC",
            "ounces": "OUNC",
            "pint": "PINT",
            "pints": "PINT",
            "quart": "QUAR",
            "quarts": "QUAR",
            "quartz": "QUAR",
        }
        return codes.get(key, "")

    @staticmethod
    def parse_size_number_unit(text, unit_for_dimension=False):
        raw = normalize_punctuation(text).replace('"', " in")
        match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?(?:\s+\d+/\d+)?|\d+/\d+)\s*([A-Za-z ]+)?\s*$", raw)
        if not match:
            return normalize_punctuation(text), ""
        value = match.group(1).strip()
        unit = (match.group(2) or "").strip()
        code = AsiBot.dimension_unit_code(unit) if unit_for_dimension else AsiBot.size_measure_unit_code(unit)
        return value, code

    @staticmethod
    def parse_size_values_list(text):
        raw = normalize_punctuation(text)
        if not raw:
            return []
        raw = re.sub(r"^(custom|standard)\s*:\s*", "", raw, flags=re.I)
        return split_multi_value(raw, seps=(",",))

    @staticmethod
    def parse_key_value_size(text):
        pairs = []
        for part in re.split(r"[;,]", normalize_punctuation(text)):
            if not part.strip() or not re.search(r"[:=]", part):
                continue
            key, value = re.split(r"[:=]", part, 1)
            pairs.append((key.strip(), value.strip()))
        return pairs

    @staticmethod
    def build_product_size_payload(row):
        select_size = safe_str(row.get("Select Size", ""))
        size_values = safe_str(row.get("Size_Values", row.get("Size Values", "")))
        size_code = AsiBot.size_type_code(select_size)
        payload = {
            "selectSize": select_size,
            "sizeCode": size_code,
            "rawValues": size_values,
            "dimensions": [],
            "volume": [],
            "capacity": [],
            "option": "",
            "customValues": [],
            "apparelValues": [],
        }
        if not size_code or size_code == "NONE" or not size_values:
            return payload

        if size_code == "DIMS":
            pairs = AsiBot.parse_key_value_size(size_values)
            if not pairs:
                nums = re.findall(r"[0-9]+(?:\.[0-9]+)?(?:\s+\d+/\d+)?|\d+/\d+", size_values)
                pairs = list(zip(["L", "W", "H"], nums[:3]))
                default_unit = "INCH" if '"' in size_values or "×" in size_values or "x" in size_values.lower() else ""
            else:
                default_unit = ""
            for key, value_text in pairs:
                value, unit = AsiBot.parse_size_number_unit(value_text, unit_for_dimension=True)
                payload["dimensions"].append({
                    "dimension": AsiBot.size_dimension_name(key),
                    "value": value,
                    "unit": unit or default_unit,
                })
            return payload

        if size_code in {"CAPS", "SVWT"}:
            target = "capacity" if size_code == "CAPS" else "volume"
            for value_text in split_multi_value(size_values, seps=(",", ";")):
                value, unit = AsiBot.parse_size_number_unit(value_text)
                payload[target].append({"value": value, "unit": unit})
            return payload

        if size_code == "SOTH":
            lowered = AsiBot.normalize_size_label(size_values)
            if lowered == "one size":
                payload["option"] = "One Size"
            elif lowered.startswith("custom"):
                payload["option"] = "Custom"
                payload["customValues"] = AsiBot.parse_size_values_list(size_values)
            else:
                payload["option"] = "Custom"
                payload["customValues"] = AsiBot.parse_size_values_list(size_values)
            return payload

        if size_code == "SSNM":
            lowered = AsiBot.normalize_size_label(size_values)
            if lowered in {"one size", "adjustable"}:
                payload["option"] = "One Size" if lowered == "one size" else "Adjustable"
            else:
                payload["option"] = "Standard"
                payload["customValues"] = AsiBot.parse_size_values_list(size_values)
            return payload

        if size_code in {"MENS", "MENT", "UNIS", "WOMS", "WOMT", "YOUT", "BOYS", "GIRL"}:
            lowered = AsiBot.normalize_size_label(size_values)
            if lowered == "osfa":
                payload["option"] = "OSFA"
            else:
                payload["option"] = "Standard"
                payload["customValues"] = AsiBot.parse_size_values_list(size_values)
            return payload

        if size_code == "SANS":
            name_map = {"neck": "Neck Sizes Only", "sleeve": "(32/33) Sleeve"}
            for key, value in AsiBot.parse_key_value_size(size_values):
                payload["apparelValues"].append({
                    "name": name_map.get(AsiBot.normalize_size_label(key), key),
                    "value": value,
                })
            return payload

        if size_code == "SAWI":
            name_map = {"waist": "Waist", "inseam": "Inseam"}
            for key, value in AsiBot.parse_key_value_size(size_values):
                payload["apparelValues"].append({
                    "name": name_map.get(AsiBot.normalize_size_label(key), key),
                    "value": value,
                })
            return payload

        if size_code in {"SABR", "SAHU", "SAIT"}:
            payload["customValues"] = AsiBot.parse_size_values_list(size_values)
            return payload

        return payload

    @staticmethod
    def split_excel_multi(value, seps=(",",)):
        values = split_multi_value(value, seps=seps)
        ignored = {"", "none", "n/a", "na", "null", "无"}
        return [item for item in values if AsiBot.normalize_size_label(item) not in ignored]

    @staticmethod
    def shape_search_filter_code(value):
        raw = AsiBot.normalize_size_label(value)
        if not raw:
            return ""
        starts_values = {"1", "start", "starts", "starts with", "startswith"}
        contains_values = {"2", "contain", "contains"}
        if raw in starts_values:
            return "startsWith"
        if raw in contains_values:
            return "contains"
        return ""

    @staticmethod
    def shape_option_codes(value):
        result = []
        option_map = {
            "1": "Any",
            "any": "Any",
            "any shape": "Any",
            "2": "Custom",
            "custom": "Custom",
            "custom shape": "Custom",
        }
        for item in AsiBot.split_excel_multi(value, seps=(",", ";")):
            option = option_map.get(AsiBot.normalize_size_label(item))
            if option and option not in result:
                result.append(option)
        return result

    @staticmethod
    def parse_attribute_weights(value):
        weights = []
        for item in AsiBot.split_excel_multi(value, seps=(",", ";")):
            amount, unit = AsiBot.parse_size_number_unit(item)
            weights.append({"value": amount, "unit": unit})
        return weights

    @staticmethod
    def canonical_product_color(value):
        key = AsiBot.normalize_size_label(value)
        aliases = {
            "assorted": "Assorted",
            "black": "Black",
            "blue": "Blue",
            "brown": "Brown",
            "clear": "Clear",
            "gray": "Gray",
            "grey": "Gray",
            "green": "Green",
            "metals": "Metals",
            "multi color": "Multi color",
            "multicolor": "Multi color",
            "orange": "Orange",
            "pink": "Pink",
            "purple": "Purple",
            "red": "Red",
            "white": "White",
            "yellow": "Yellow",
            "burgundy": "Burgundy",
            "gold": "Gold",
            "light blue": "Light Blue",
            "maroon": "Maroon",
            "navy": "Navy Blue",
            "navy blue": "Navy Blue",
            "royal blue": "Royal Blue",
            "silver": "Silver",
            "teal": "Teal",
        }
        return aliases.get(key, safe_str(value).strip().title())

    @staticmethod
    def parse_product_colors(value):
        colors = []
        seen = set()
        for item in AsiBot.split_excel_multi(value, seps=(",", ";")):
            color = AsiBot.canonical_product_color(item)
            key = AsiBot.normalize_size_label(color)
            if not color or key in seen:
                continue
            seen.add(key)
            colors.append(color)
        return colors

    @staticmethod
    def build_material_entries(row):
        materials = AsiBot.split_excel_multi(
            AsiBot.row_value(row, "Material"),
            seps=(",", ";"),
        )
        aliases = AsiBot.split_excel_multi(
            AsiBot.row_value(
                row,
                "Material Custom Name",
                "Material_Custom_Name",
                "Material Alias",
            ),
            seps=(",", ";"),
        )
        return [
            {
                "material": material,
                "alias": aliases[index] if index < len(aliases) else material,
            }
            for index, material in enumerate(materials)
        ]

    @staticmethod
    def build_attributes_payload(row):
        return {
            "colors": AsiBot.parse_product_colors(AsiBot.row_value(row, "Product_Color", "Product Color")),
            "material": AsiBot.row_value(row, "Material"),
            "materials": AsiBot.build_material_entries(row),
            "shapes": AsiBot.split_excel_multi(AsiBot.row_value(row, "Shapes", "Shape"), seps=(",",)),
            "shapeSearchFilter": AsiBot.shape_search_filter_code(
                AsiBot.row_value(row, "Search Filter Option", "Shapes Search Filter")
            ),
            "shapeOptions": AsiBot.shape_option_codes(
                AsiBot.row_value(row, "Any Shape/Custom Shape", "Shape Options")
            ),
            "weights": AsiBot.parse_attribute_weights(
                AsiBot.row_value(row, "Weight", "Item Weight")
            ),
        }

    @staticmethod
    def parse_imprint_color_codes(value):
        flags = {"standard": False, "custom": False, "pms": False}
        for item in AsiBot.split_excel_multi(value, seps=(",",)):
            key = AsiBot.normalize_size_label(item)
            if key in {"1", "standard", "standard color", "standard colors"}:
                flags["standard"] = True
            elif key in {"2", "custom", "custom color", "custom colors", "customs color", "customs colors"}:
                flags["custom"] = True
            elif key in {"3", "pms", "pms color", "pms colors"}:
                flags["pms"] = True
        return flags

    @staticmethod
    def parse_artwork_information_items(value):
        mapping = {
            "1": "Virtual Proof",
            "virtual proof": "Virtual Proof",
            "2": "Paper Proof",
            "paper proof": "Paper Proof",
            "3": "Art Services",
            "art service": "Art Services",
            "art services": "Art Services",
            "4": "Pre-production Proof",
            "pre-production proof": "Pre-production Proof",
            "pre production proof": "Pre-production Proof",
        }
        result = []
        seen = {}
        for item in AsiBot.split_excel_multi(value, seps=(",", ";")):
            text = normalize_punctuation(item)
            code = text
            comment = ""
            if ":" in text:
                code, comment = text.split(":", 1)
            name = mapping.get(AsiBot.normalize_size_label(code), safe_str(code))
            if not name:
                continue
            comment = safe_str(comment)
            entry = seen.get(name)
            if not entry:
                entry = {"name": name, "comment": comment}
                result.append(entry)
                seen[name] = entry
            elif comment:
                entry["comment"] = comment
        return result

    @staticmethod
    def parse_artwork_information(value):
        return [item["name"] for item in AsiBot.parse_artwork_information_items(value)]

    @staticmethod
    def build_imprint_payload(row):
        imprint_color_raw = AsiBot.row_value(row, "Imprint Colors", "Imprint_Color", "Imprint Color")
        colors = AsiBot.split_excel_multi(AsiBot.row_value(row, "Colors", "Imprint Color Names"), seps=(",",))
        imprint_location_raw = AsiBot.row_value(row, "Imprint Location", "Imprint_Location")
        imprint_locations = AsiBot.merge_unique_values(
            AsiBot.split_excel_multi(imprint_location_raw, seps=(",", ";")),
            AsiBot.parse_setup_charge_locations(
                AsiBot.row_value(row, "Set-up Change", "Set-up Charge", "Setup Charge")
            ),
        )
        artwork_items = AsiBot.parse_artwork_information_items(
            AsiBot.row_value(row, "Artwork Information", "Artwork_Information")
        )
        return {
            "methods": AsiBot.split_excel_multi(
                AsiBot.row_value(row, "Select Method", "Imprint_Method", "Imprint Method"),
                seps=(",",),
            ),
            "personalizationMethods": AsiBot.split_excel_multi(
                AsiBot.row_value(row, "Personalization Available", "Personalization_Available"),
                seps=(",",),
            ),
            "hasPersonalizationAvailable": bool(
                AsiBot.row_value(row, "Personalization Available", "Personalization_Available")
            ),
            "imprintColors": AsiBot.parse_imprint_color_codes(imprint_color_raw),
            "hasImprintColorCodes": bool(AsiBot.split_excel_multi(imprint_color_raw, seps=(",",))),
            "colors": colors,
            "colorTokens": AsiBot.imprint_color_token_items(colors),
            "productionTime": AsiBot.row_value(row, "Production time", "Production Time"),
            "rushService": AsiBot.row_value(row, "Rush Service Offered", "Rush_Service_Offered"),
            "sameDayService": AsiBot.row_value(row, "Same Day Service Offered", "Same_Day_Service_Offered"),
            "imprintSize": AsiBot.row_value(row, "Imprint Size", "Imprint_Size"),
            "imprintLocation": imprint_location_raw,
            "imprintLocations": imprint_locations,
            "additionalColorsInfo": AsiBot.row_value(
                row,
                "Additional Colors Information",
                "Additional_Colors_Information",
            ),
            "artwork": [item["name"] for item in artwork_items],
            "artworkComments": {
                item["name"]: item["comment"]
                for item in artwork_items
                if item.get("comment")
            },
        }

    @staticmethod
    def price_code_value(value):
        text = safe_str(value).upper().strip()
        if not text:
            return ""
        if re.fullmatch(r"[A-Z]", text):
            return text
        slash_match = re.search(r"/\s*([A-Z])\b", text)
        if slash_match:
            return slash_match.group(1)
        lead_match = re.match(r"\s*([A-Z])\b", text)
        return lead_match.group(1) if lead_match else ""

    @staticmethod
    def merge_unique_values(*groups):
        result = []
        seen = set()
        for group in groups:
            for value in group or []:
                text = safe_str(value)
                key = AsiBot.normalize_size_label(text)
                if text and key not in seen:
                    result.append(text)
                    seen.add(key)
        return result

    @staticmethod
    def parse_setup_charge_locations(value):
        locations = []
        seen = set()
        text = normalize_punctuation(value)
        for part in [item.strip() for item in re.split(r"[,;]", text) if item.strip()]:
            if ":" not in part:
                continue
            name, amount = part.split(":", 1)
            location = safe_str(name)
            if not location or not safe_str(amount):
                continue
            key = AsiBot.normalize_size_label(location)
            if key not in seen:
                locations.append(location)
                seen.add(key)
        return locations

    @staticmethod
    def parse_setup_charge_codes(value, locations):
        location_names = [safe_str(location) for location in locations if safe_str(location)]
        by_name = {AsiBot.normalize_size_label(location): location for location in location_names}
        default_code = AsiBot.price_code_value(value)
        keyed_codes = {}
        text = normalize_punctuation(value)
        for part in [item.strip() for item in re.split(r"[,;]", text) if item.strip()]:
            if ":" not in part:
                continue
            name, code = part.split(":", 1)
            location = by_name.get(AsiBot.normalize_size_label(name))
            code_value = AsiBot.price_code_value(code)
            if location and code_value:
                keyed_codes[AsiBot.normalize_size_label(location)] = code_value
        return keyed_codes, default_code

    @staticmethod
    def parse_setup_charges(value, locations, setup_charge_codes=""):
        location_names = AsiBot.merge_unique_values(locations, AsiBot.parse_setup_charge_locations(value))
        by_name = {AsiBot.normalize_size_label(location): location for location in location_names}
        keyed_codes, default_code = AsiBot.parse_setup_charge_codes(setup_charge_codes, location_names)
        charges = []
        text = normalize_punctuation(value)
        for part in [item.strip() for item in re.split(r"[,;]", text) if item.strip()]:
            if ":" not in part:
                continue
            name, amount = part.split(":", 1)
            location = by_name.get(AsiBot.normalize_size_label(name))
            amount = safe_str(amount)
            if location and amount:
                code = keyed_codes.get(AsiBot.normalize_size_label(location), default_code)
                charges.append({"location": location, "amount": amount, "code": code})
        return charges

    @staticmethod
    def _path_stem(path_value):
        """跨平台提取文件名主干（macOS 上 Path 不解析 Windows 反斜杠路径）。"""
        text = safe_str(path_value).replace("\\", "/")
        name = text.rsplit("/", 1)[-1]
        if "." in name:
            return name.rsplit(".", 1)[0]
        return name

    @staticmethod
    def media_colors_from_name(image_file, available_colors=None):
        stem = AsiBot._path_stem(image_file)
        if not stem or not available_colors:
            return []
        text = normalize_punctuation(stem).lower()
        bracket_match = re.search(r"\[([^\]]+)\]\s*$", text)
        if bracket_match:
            text = bracket_match.group(1)
        else:
            text = re.sub(r"^\s*\d+(?:[_\-\s.]+|$)", " ", text)
        text = re.sub(r"[_+\-,;()]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        color_patterns = []
        for color in available_colors:
            color_text = safe_str(color)
            if not color_text:
                continue
            aliases = [color_text]
            match = re.match(r"^\s*(.*?)\s*\((.*?)\)\s*$", color_text)
            if match:
                aliases.extend([match.group(1), match.group(2)])
            for alias in aliases:
                normalized_alias = normalize_punctuation(alias).lower()
                normalized_alias = re.sub(r"[_+\-,;()]+", " ", normalized_alias)
                normalized_alias = re.sub(r"\s+", " ", normalized_alias).strip()
                if normalized_alias:
                    color_patterns.append((color_text, normalized_alias, rf"\b{re.escape(normalized_alias)}\b"))
        color_patterns.sort(key=lambda item: len(item[1]), reverse=True)
        colors = []
        matches = []
        remaining = text
        seen = set()
        for color, _alias, pattern in color_patterns:
            if color in seen:
                continue
            match = re.search(pattern, remaining)
            if match:
                matches.append((match.start(), color))
                seen.add(color)
                remaining = re.sub(pattern, " ", remaining)
        for _position, color in sorted(matches, key=lambda item: item[0]):
            colors.append(color)
        return colors

    @staticmethod
    def media_tags_for_files(image_files, available_colors=None):
        tags = []
        for image_file in image_files or []:
            stem = AsiBot._path_stem(image_file)
            if stem:
                item = {"stem": stem}
                colors = AsiBot.media_colors_from_name(image_file, available_colors)
                if colors:
                    item["colors"] = colors
                tags.append(item)
        return tags

    @staticmethod
    def build_pricing_payload(row):
        prices = []
        for i in range(1, MAX_PRICE_TIERS + 1):
            quantity = safe_str(row.get(f"Q{i}", ""))
            net_cost = safe_str(row.get(f"P{i}", ""))
            if quantity and net_cost:
                prices.append({"quantity": quantity, "netCost": net_cost})

        price_code = AsiBot.row_value(row, "Price Codes", "Price_Codes")
        imprint_locations = AsiBot.split_excel_multi(
            AsiBot.row_value(row, "Imprint Location", "Imprint_Location"),
            seps=(",", ";"),
        )
        setup_charge_raw = AsiBot.row_value(row, "Set-up Change", "Set-up Charge", "Setup Charge")
        setup_locations = AsiBot.merge_unique_values(
            imprint_locations,
            AsiBot.parse_setup_charge_locations(setup_charge_raw),
        )
        setup_charge_code = AsiBot.row_value(
            row,
            "Set-up Change Codes",
            "Set-up Change Code",
            "Set-up Charge Codes",
            "Set-up Charge Code",
            "Setup Charge Codes",
            "Setup Charge Code",
        )
        return {
            "prices": prices,
            "priceCode": price_code,
            "priceCodeValue": AsiBot.price_code_value(price_code),
            "priceIncludes": AsiBot.row_value(row, "Price_Includes", "Price Includes"),
            "canOrderLessThanMinimum": True,
            "setupChargeCode": setup_charge_code,
            "setupChargeCodeValue": AsiBot.price_code_value(setup_charge_code),
            "setupCharges": AsiBot.parse_setup_charges(
                setup_charge_raw,
                setup_locations,
                setup_charge_code,
            ),
        }

    @staticmethod
    def imprint_color_token_items(colors):
        return [
            {"id": -1000 - index, "key": -1000 - index, "name": color}
            for index, color in enumerate(colors)
        ]

    def fill_basic_shipping(self, row):
        """Fill Basic Details > Warehouses / Shipping Estimate."""
        payload = self.build_shipping_payload(row)
        if not any(v for v in payload.values()):
            return

        logger.info("  填写 Warehouses / Shipping Estimate...")
        result = self.tab.run_js("""
            var payload = JSON.parse(arguments[0] || "{}");
            var rootEl = document.getElementById('product-info-view') || document.body;
            var root = window.ko && ko.dataFor(rootEl);
            if (!root) return {ok:false, error:"product-info-view ko model not found"};

            function setObs(target, key, value) {
                if (!target || value === undefined || value === null || value === "") return false;
                if (typeof target[key] === "function") {
                    target[key](value);
                    return true;
                }
                target[key] = value;
                return true;
            }
            function unwrap(value) {
                return (typeof value === "function") ? value() : value;
            }

            var shippingEstimate = unwrap(root.shippingEstimate);
            var changed = [];
            if (shippingEstimate) {
                [
                    "numItems", "numItemUOM",
                    "dim1Value", "dim1UOM",
                    "dim2Value", "dim2UOM",
                    "dim3Value", "dim3UOM",
                    "shipWeight", "shipUOM"
                ].forEach(function(key) {
                    if (setObs(shippingEstimate, key, payload[key])) changed.push(key);
                });
            }

            var bill = String(payload.shipperBillsBy || "").trim();
            if (bill) {
                setObs(root, "weightOfPackage", bill === "1");
                setObs(root, "sizeOfPackage", bill === "2");
                setObs(root, "isFreeShipping", bill === "3");
                changed.push("shipperBillsBy:" + bill);
            }

            var packagingTargets = (payload.packaging || []).map(function(v) {
                return String(v || "").trim().toLowerCase().replace(/\\s+/g, " ");
            }).filter(Boolean);
            var packagingTargetSet = {};
            packagingTargets.forEach(function(name) { packagingTargetSet[name] = true; });
            var packagingNames = {};
            (unwrap(root.packagingList) || []).forEach(function(item) {
                var name = String(unwrap(item.name) || "").trim();
                if (name) packagingNames[name.toLowerCase().replace(/\\s+/g, " ")] = true;
            });
            var packagingMatched = [];

            var packagingDomMatched = [];
            document.querySelectorAll('#product-info-view .checkbox').forEach(function(box) {
                var input = box.querySelector('input[type="checkbox"]');
                var span = box.querySelector('span');
                var name = span ? String(span.textContent || "").trim() : "";
                var normalizedName = name.toLowerCase().replace(/\\s+/g, " ");
                if (!input || !name || !packagingNames[normalizedName]) return;
                var shouldSelect = !!packagingTargetSet[normalizedName];
                if (shouldSelect) {
                    packagingMatched.push(name);
                    packagingDomMatched.push({name: name, checkedBefore: !!input.checked});
                }
                if (!!input.checked !== shouldSelect) {
                    input.click();
                }
                input.dispatchEvent(new Event('input', {bubbles:true}));
                input.dispatchEvent(new Event('change', {bubbles:true}));
                if (shouldSelect) {
                    packagingDomMatched[packagingDomMatched.length - 1].checkedAfter = !!input.checked;
                }
            });

            if (!packagingDomMatched.length && packagingTargets.length) {
                var packagingList = unwrap(root.packagingList) || [];
                packagingList.forEach(function(item) {
                    var name = String(unwrap(item.name) || "").trim();
                    var normalizedName = name.toLowerCase().replace(/\\s+/g, " ");
                    var shouldSelect = !!packagingTargetSet[normalizedName];
                    if (shouldSelect) packagingMatched.push(name);
                    setObs(item, "selected", shouldSelect);
                    if (shouldSelect && typeof root.selectPackagingOption === "function") {
                        root.selectPackagingOption(item);
                    }
                });
            }

            var productInfo = unwrap(root.productInfo);
            if (productInfo) {
                if (payload.additionalShippingInfo) {
                    setObs(productInfo, "addtionalShippingInfo", payload.additionalShippingInfo);
                    changed.push("additionalShippingInfo");
                }
            }

            if (typeof root.hasShippingEstimate === "function") {
                root.hasShippingEstimate(true);
            }

            document.querySelectorAll('#product-info-view input, #product-info-view select, #product-info-view textarea').forEach(function(el) {
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new Event('blur', {bubbles:true}));
            });

            return {
                ok: true,
                changed: changed,
                packagingMatched: packagingMatched,
                packagingDomMatched: packagingDomMatched,
                shippingEstimate: shippingEstimate ? {
                    numItems: unwrap(shippingEstimate.numItems),
                    numItemUOM: unwrap(shippingEstimate.numItemUOM),
                    dim1Value: unwrap(shippingEstimate.dim1Value),
                    dim1UOM: unwrap(shippingEstimate.dim1UOM),
                    dim2Value: unwrap(shippingEstimate.dim2Value),
                    dim2UOM: unwrap(shippingEstimate.dim2UOM),
                    dim3Value: unwrap(shippingEstimate.dim3Value),
                    dim3UOM: unwrap(shippingEstimate.dim3UOM),
                    shipWeight: unwrap(shippingEstimate.shipWeight),
                    shipUOM: unwrap(shippingEstimate.shipUOM)
                } : null
            };
        """, json.dumps(payload, ensure_ascii=False))
        logger.info(f"  Warehouses结果: {result}")

    def fill_basic_origins(self, row):
        """Fill Basic Details > Manufactured/Assembled/Decorated In."""
        payload = self.build_origin_payload(row)
        if not any(v for v in payload.values()):
            return

        logger.info("  填写 Manufactured/Assembled/Decorated In...")
        result = self.tab.run_js("""
            var payload = JSON.parse(arguments[0] || "{}");
            var rootEl = document.getElementById('product-info-view') || document.body;
            var root = window.ko && ko.dataFor(rootEl);

            function unwrap(value) {
                return (typeof value === "function") ? value() : value;
            }
            function setObs(target, key, value) {
                if (!target || value === undefined || value === null) return false;
                if (typeof target[key] === "function") {
                    target[key](value);
                    return true;
                }
                target[key] = value;
                return true;
            }
            function norm(value) {
                var text = String(value || "").trim().toLowerCase();
                text = text.replace(/&amp;/g, "&");
                text = text.replace(/[.]/g, "");
                text = text.replace(/\\s+/g, " ");
                if (text === "usa" || text === "us" || text === "united states") return "usa";
                return text;
            }
            function targetSet(values) {
                var set = {};
                if (typeof values === "string") values = values ? [values] : [];
                (values || []).forEach(function(value) {
                    var key = norm(value);
                    if (key) set[key] = true;
                });
                return set;
            }
            function setNativeValue(el, value) {
                var setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
                if (setter && setter.set) setter.set.call(el, value);
                else el.value = value;
                el.dispatchEvent(new Event("input", {bubbles:true}));
                el.dispatchEvent(new Event("change", {bubbles:true}));
                el.dispatchEvent(new Event("blur", {bubbles:true}));
            }
            function chooseSelect(bindNeedle, wanted) {
                wanted = String(wanted || "").trim();
                if (!wanted) return {wanted: wanted, matched: ""};
                var wantedKey = norm(wanted);
                var selects = Array.from(document.querySelectorAll('#product-info-view select[data-bind*="' + bindNeedle + '"]'));
                for (var s = 0; s < selects.length; s++) {
                    var sel = selects[s];
                    for (var i = 0; i < sel.options.length; i++) {
                        var opt = sel.options[i];
                        if (norm(opt.textContent) === wantedKey || norm(opt.value) === wantedKey) {
                            setNativeValue(sel, opt.value);
                            return {wanted: wanted, matched: String(opt.textContent || "").trim(), value: opt.value};
                        }
                    }
                }
                return {wanted: wanted, matched: ""};
            }

            var manufacturedTargets = targetSet(payload.manufacturedIn);
            var manufacturedMatched = [];

            var manufacturedDomMatched = [];
            var menu = document.querySelector('#manufacturedInList');
            var container = menu ? menu.parentElement : document;
            Array.from(container.querySelectorAll('label.checkbox')).forEach(function(label) {
                var input = label.querySelector('input[type="checkbox"]');
                var span = label.querySelector('span');
                var name = span ? String(span.textContent || "").trim() : "";
                if (!input || !name) return;
                var shouldSelect = !!manufacturedTargets[norm(name)];
                if (shouldSelect) {
                    manufacturedMatched.push(name);
                    manufacturedDomMatched.push(name);
                }
                if (!!input.checked !== shouldSelect) input.click();
                input.dispatchEvent(new Event("input", {bubbles:true}));
                input.dispatchEvent(new Event("change", {bubbles:true}));
            });

            if (!manufacturedDomMatched.length && root) {
                var originsList = unwrap(root.originsList) || [];
                originsList.forEach(function(item) {
                    var name = String(unwrap(item.name) || "").trim();
                    var selected = !!manufacturedTargets[norm(name)];
                    if (selected) manufacturedMatched.push(name);
                    setObs(item, "selected", selected);
                    if (selected && typeof root.selectOriginOption === "function") {
                        root.selectOriginOption(item);
                    }
                });
            }

            var assembled = chooseSelect("assembledInList", payload.assembledIn);
            var decorated = chooseSelect("decoratedInList", payload.decoratedIn);

            document.querySelectorAll('#product-info-view input, #product-info-view select').forEach(function(el) {
                el.dispatchEvent(new Event('blur', {bubbles:true}));
            });

            return {
                ok: true,
                manufacturedMatched: manufacturedMatched,
                manufacturedDomMatched: manufacturedDomMatched,
                assembled: assembled,
                decorated: decorated
            };
        """, json.dumps(payload, ensure_ascii=False))
        logger.info(f"  产地结果: {result}")

    def set_distributor_only_view(self, enabled):
        """Set ASI Basic Details > Distributor Only View."""
        return self.tab.run_js("""
            var desired = Boolean(arguments[0]);
            var rootEl = document.getElementById('product-info-view') || document.body;
            var vm = window.ko && ko.dataFor(rootEl);
            if (!vm) return {ok:false, error:'product-info-view ko model not found'};

            var observableSet = false;
            if (typeof vm.distOnlyView === 'function') {
                vm.distOnlyView(desired);
                observableSet = true;
            }

            var checkbox = Array.from(document.querySelectorAll(
                '#product-info-view input[type="checkbox"], body input[type="checkbox"]'
            )).find(function(input) {
                return (input.getAttribute('data-bind') || '').indexOf('distOnlyView') !== -1;
            });
            if (checkbox && Boolean(checkbox.checked) !== desired) {
                checkbox.checked = desired;
                checkbox.dispatchEvent(new Event('input', {bubbles:true}));
                checkbox.dispatchEvent(new Event('change', {bubbles:true}));
                checkbox.dispatchEvent(new Event('blur', {bubbles:true}));
            }
            if (typeof vm.isDirty === 'function') vm.isDirty(true);

            var observableValue = typeof vm.distOnlyView === 'function'
                ? Boolean(vm.distOnlyView())
                : null;
            var checkboxValue = checkbox ? Boolean(checkbox.checked) : null;
            return {
                ok: observableValue === desired || checkboxValue === desired,
                observableSet: observableSet,
                observable: observableValue,
                checkbox: checkboxValue,
                desired: desired
            };
        """, bool(enabled))

    def set_basic_product_flags(self, product_flags):
        """Apply the five checkbox flags shown at the top of Basic Details."""
        flags = normalize_product_flags(product_flags)
        return self.tab.run_js("""
            var desired = {
                distributorOnly: Boolean(arguments[0]),
                newProduct: Boolean(arguments[1]),
                seoProduct: Boolean(arguments[2]),
                closeOut: Boolean(arguments[3]),
                productConfirmed: Boolean(arguments[4])
            };
            var rootEl = document.getElementById('product-info-view') || document.body;
            var vm = rootEl && window.ko ? ko.dataFor(rootEl) : null;
            function unwrap(value) {
                return typeof value === 'function' ? value() : value;
            }
            function write(owner, key, value) {
                if (!owner || typeof owner[key] !== 'function') return false;
                try {
                    owner[key](value);
                    return true;
                } catch (error) {
                    return false;
                }
            }
            function syncCheckbox(binding, checked) {
                var input = Array.from(document.querySelectorAll(
                    '#product-info-view input[type="checkbox"], body input[type="checkbox"]'
                )).find(function(candidate) {
                    return (candidate.getAttribute('data-bind') || '').indexOf(binding) !== -1;
                });
                if (input && Boolean(input.checked) !== checked) {
                    input.checked = checked;
                    input.dispatchEvent(new Event('input', {bubbles:true}));
                    input.dispatchEvent(new Event('change', {bubbles:true}));
                    input.dispatchEvent(new Event('blur', {bubbles:true}));
                }
                return input ? Boolean(input.checked) : null;
            }

            var productInfo = vm ? unwrap(vm.productInfo) : null;
            var currentNew = vm ? unwrap(vm.newProductFlag) : null;
            var newValue = typeof currentNew === 'string'
                ? (desired.newProduct ? 'Y' : 'N')
                : desired.newProduct;
            var observableWrites = {
                distOnlyView: write(vm, 'distOnlyView', desired.distributorOnly),
                newProductFlag: write(vm, 'newProductFlag', newValue),
                isProductSEOEnabled: write(vm, 'isProductSEOEnabled', desired.seoProduct),
                isCloseOut: write(vm, 'isCloseOut', desired.closeOut),
                isPriceConfirmed: write(productInfo, 'isPriceConfirmed', desired.productConfirmed)
            };
            var checkboxValues = {
                distOnlyView: syncCheckbox('distOnlyView', desired.distributorOnly),
                newProductFlag: syncCheckbox('newProductFlag', desired.newProduct),
                isProductSEOEnabled: syncCheckbox('isProductSEOEnabled', desired.seoProduct),
                isCloseOut: syncCheckbox('isCloseOut', desired.closeOut),
                isPriceConfirmed: syncCheckbox('isPriceConfirmed', desired.productConfirmed)
            };
            if (vm && typeof vm.isDirty === 'function') vm.isDirty(true);
            return {ok: !!vm, observableWrites: observableWrites, checkboxes: checkboxValues};
        """,
            flags["distributor_only_view"],
            flags["new_product"],
            flags["seo_product"],
            flags["close_out"],
            flags["product_confirmed"],
        )

    def set_prop65_no_chemicals(self, enabled):
        """Set the two ASI Prop 65 checkboxes to a non-conflicting state."""
        return self.tab.run_js("""
            var desired = Boolean(arguments[0]);
            var inputs = Array.from(document.querySelectorAll(
                '#product-info-view input[type="checkbox"], body input[type="checkbox"]'
            ));
            function byBinding(name) {
                return inputs.find(function(input) {
                    return (input.getAttribute('data-bind') || '').indexOf(name) !== -1;
                }) || null;
            }
            var noChemicals = byBinding('noProp65Selected');
            var warnings = byBinding('prop65Selected');
            if (noChemicals && Boolean(noChemicals.checked) !== desired) noChemicals.click();
            if (desired && warnings && warnings.checked) warnings.click();
            return {
                ok: !!noChemicals,
                desired: desired,
                noChemicals: noChemicals ? Boolean(noChemicals.checked) : null,
                warnings: warnings ? Boolean(warnings.checked) : null
            };
        """, bool(enabled))

    def fill_basic_details(
        self,
        row,
        distributor_only_view=False,
        upload_controls=None,
        product_flags=None,
    ):
        """填充 Basic Details Tab"""
        logger.info("填充 Basic Details...")
        click_tab(self.tab, "#/product/info")
        flags = normalize_product_flags(product_flags) if product_flags is not None else None
        if flags is not None:
            flags_result = self.set_basic_product_flags(flags)
            logger.info(f"  产品勾选项: {flags_result}")
        elif field_mode(upload_controls, "basic_details", "distributor_only_view") != MODE_SKIP:
            visibility_result = self.set_distributor_only_view(distributor_only_view)
            logger.info(f"  Distributor Only View: {visibility_result}")

        include_prop65 = field_mode(upload_controls, "basic_details", "prop65") != MODE_SKIP
        prop65_no_chemicals = (
            flags["prop65_no_chemicals"]
            if flags is not None
            else runtime_switch_value(upload_controls, "basic_details", "prop65")
        )
        text_result = self.fill_basic_text_fields(
            row,
            include_prop65=include_prop65 and prop65_no_chemicals,
        )
        if text_result:
            logger.info(f"  Basic文本字段: {text_result}")
        if include_prop65 and not prop65_no_chemicals:
            prop65_result = self.set_prop65_no_chemicals(False)
            logger.info(f"  Prop 65 无化学品声明: {prop65_result}")

        cat = safe_str(row.get("Category", ""))
        if cat:
            logger.info(f"  设置分类: {cat}")
            fill_token_input(self.tab, "productCategories-Supplier", cat)

        kw = safe_str(row.get("Keywords", ""))
        if kw:
            logger.info(f"  设置关键词...")
            fill_token_input(self.tab, "productKeywords", kw)

        thm = self.row_value(row, "Theme", "Product Theme")
        if thm:
            logger.info(f"  设置主题: {thm}")
            result = self.fill_product_theme(thm)
            logger.info(f"  Product Theme结果: {result}")

        self.fill_basic_origins(row)
        self.fill_basic_shipping(row)

        comp_cert = safe_str(row.get("Comp_Cert", ""))
        if comp_cert:
            result = self.tab.run_js("""
                var value = arguments[0] || "";
                var inputs = Array.from(document.querySelectorAll('#product-info-view input[type="text"], #product-info-view textarea'));
                function isVisible(el) { return !!(el && (el.offsetParent || el.getClientRects().length)); }
                function setValue(el, val) {
                    var proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                    var setter = Object.getOwnPropertyDescriptor(proto, "value");
                    if (setter && setter.set) setter.set.call(el, val);
                    else el.value = val;
                    el.dispatchEvent(new Event("input", {bubbles:true}));
                    el.dispatchEvent(new Event("change", {bubbles:true}));
                    el.dispatchEvent(new Event("blur", {bubbles:true}));
                }
                for (var i = 0; i < inputs.length; i++) {
                    var txt = (inputs[i].closest(".control-group,.subsection,.controls-row") || {}).textContent || "";
                    if (isVisible(inputs[i]) && /Certifications and Compliance/i.test(txt)) {
                        setValue(inputs[i], value);
                        return true;
                    }
                }
                return false;
            """, comp_cert)
            logger.info(f"  Certifications and Compliance: {result}")

    def fill_basic_text_fields(self, row, include_prop65=True):
        """Fill Basic Details text fields and their Knockout observables."""
        payload = self.build_basic_details_payload(row, include_prop65=include_prop65)
        if not any(payload.values()):
            return None

        return self.tab.run_js("""
            var payload = JSON.parse(arguments[0] || "{}");
            var rootEl = document.getElementById('product-info-view') || document.body;
            var vm = window.ko && ko.dataFor(rootEl);

            function unwrap(value) {
                try {
                    if (value && typeof value.peek === 'function') return value.peek();
                    return typeof value === 'function' ? value() : value;
                } catch (e) {
                    return null;
                }
            }
            function setObs(target, key, value) {
                if (!target || value === undefined || value === null || value === '') return false;
                if (typeof target[key] === 'function') target[key](value);
                else if (target[key] !== undefined) target[key] = value;
                else return false;
                return true;
            }
            function setNativeValue(selector, value) {
                if (!value) return false;
                var el = document.querySelector(selector);
                if (!el) return false;
                var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                var setter = Object.getOwnPropertyDescriptor(proto, 'value');
                if (setter && setter.set) setter.set.call(el, value);
                else el.value = value;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new Event('blur', {bubbles:true}));
                return true;
            }
            function isVisible(el) {
                return !!(el && (el.offsetParent || el.getClientRects().length));
            }
            function setCheckboxByText(label, desired) {
                var target = String(label || '').trim().toLowerCase();
                var inputs = Array.from(document.querySelectorAll("#product-info-view input[type='checkbox'], body input[type='checkbox']"));
                var input = inputs.find(function(candidate) {
                    var container = candidate.closest("label,.checkbox,.control-group,.controls-row,.subsection,li,div") || candidate.parentElement;
                    var text = (container && container.textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
                    return text.indexOf(target) >= 0 && isVisible(candidate);
                });
                if (!input) return false;
                if (!!input.checked !== !!desired) input.click();
                input.dispatchEvent(new Event('change', {bubbles:true}));
                input.dispatchEvent(new Event('blur', {bubbles:true}));
                return true;
            }

            var productInfo = vm && typeof vm.productInfo === 'function' ? vm.productInfo() : null;
            if (!productInfo && vm && (vm.description || vm.summary || vm.name || vm.asiProdNo)) {
                productInfo = vm;
            }

            var changed = [];
            if (payload.productNumber) {
                if (setObs(productInfo, 'asiProdNo', payload.productNumber)) changed.push('vm.productNumber');
                if (setNativeValue('#productNum', payload.productNumber)) changed.push('dom.productNumber');
            }
            if (payload.productName) {
                if (setObs(productInfo, 'name', payload.productName)) changed.push('vm.productName');
                if (setNativeValue('#productName', payload.productName)) changed.push('dom.productName');
            }
            if (payload.description) {
                if (setObs(productInfo, 'description', payload.description)) changed.push('vm.description');
                if (setNativeValue('#productDescription', payload.description)) changed.push('dom.description');
            }
            if (payload.summary) {
                if (setObs(productInfo, 'summary', payload.summary)) changed.push('vm.summary');
                if (setNativeValue('#productSummary', payload.summary)) changed.push('dom.summary');
            }
            if (payload.prop65NoChemicals) {
                [
                    'doesNotContainProp65Chemicals',
                    'productDoesNotContainProp65Chemicals',
                    'isProp65NotApplicable',
                    'prop65NoChemicals',
                    'isProp65Compliant'
                ].forEach(function(key) {
                    if (setObs(productInfo, key, true)) changed.push('vm.' + key);
                    if (setObs(vm, key, true)) changed.push('vmRoot.' + key);
                });
                if (setCheckboxByText('Product does not contain Prop 65 chemicals', true)) changed.push('dom.prop65NoChemicals');
                if (setCheckboxByText('Prop 65 warnings are applicable', false)) changed.push('dom.prop65WarningsOff');
            }

            if (productInfo && typeof productInfo.childChanged === 'function') productInfo.childChanged(true);
            if (vm && typeof vm.isDirty === 'function') vm.isDirty(true);
            return {ok: changed.length > 0, changed: changed, current: {
                description: productInfo && unwrap(productInfo.description),
                summary: productInfo && unwrap(productInfo.summary),
                prop65NoChemicals: payload.prop65NoChemicals
            }};
        """, json.dumps(payload, ensure_ascii=False))

    def fill_product_theme(self, value):
        """Select Basic Details > Product Theme using ASI's tokenInput control."""
        themes = split_multi_value(value)
        if not themes:
            return {"ok": False, "message": "empty"}
        return self.tab.run_js("""
            var themes = JSON.parse(arguments[0] || "[]");
            function text(v) { return String(v == null ? "" : v).trim(); }
            function visible(el) { return !!(el && (el.offsetParent || el.getClientRects().length)); }
            function selectedTexts() {
                return Array.from(document.querySelectorAll('#productThemesParent li.token-input-token-facebook p, #productThemesParent li.token-input-token-facebook span'))
                    .map(function(el) { return text(el.textContent); })
                    .filter(function(t) { return t && t.toLowerCase() !== 'x' && t !== '×'; });
            }
            async function findTheme(term) {
                var urls = [
                    '/api/api/lookup/themes_q?queryFilterType=startsWith&q=' + encodeURIComponent(term),
                    '/api/api/lookup/themes_q?queryFilterType=contains&q=' + encodeURIComponent(term),
                    '/api/api/lookup/themes_q?queryFilterType=startsWith&term=' + encodeURIComponent(term),
                    '/api/api/lookup/themes_q?queryFilterType=contains&term=' + encodeURIComponent(term)
                ];
                for (var u = 0; u < urls.length; u++) {
                    var response = await fetch(urls[u]);
                    if (!response.ok) continue;
                    var data = await response.json();
                    var list = Array.isArray(data) ? data : (data.Results || data.results || data.Items || data.items || []);
                    var exact = list.find(function(item) { return text(item.Value || item.value || item.Name || item.name).toLowerCase() === term.toLowerCase(); });
                    if (exact) return {Key: exact.Key || exact.key || exact.Id || exact.id, Value: exact.Value || exact.value || exact.Name || exact.name};
                    if (list.length) {
                        var first = list[0];
                        return {Key: first.Key || first.key || first.Id || first.id, Value: first.Value || first.value || first.Name || first.name};
                    }
                }
                return null;
            }
            async function run() {
                var input = document.getElementById('productThemes');
                var tokenInput = document.getElementById('token-input-productThemes');
                var added = [];
                var missing = [];
                if (!input && !tokenInput) return {ok:false, error:'productThemes token input not found', selected:selectedTexts()};
                for (var i = 0; i < themes.length; i++) {
                    var wanted = text(themes[i]);
                    if (!wanted) continue;
                    var item = await findTheme(wanted);
                    if (!item || !item.Key || !item.Value) {
                        missing.push(wanted);
                        continue;
                    }
                    try {
                        if (window.jQuery && jQuery(input).data('tokenInputObject')) {
                            jQuery(input).tokenInput('add', item);
                        } else if (tokenInput) {
                            tokenInput.focus();
                            tokenInput.value = item.Value;
                            tokenInput.dispatchEvent(new Event('input', {bubbles:true}));
                            tokenInput.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', keyCode:13, which:13, bubbles:true}));
                        }
                        added.push(item.Value);
                    } catch (e) {
                        missing.push(wanted + ': ' + e.message);
                    }
                }
                return {ok: added.length > 0, added: added, missing: missing, selected: selectedTexts()};
            }
            return run();
        """, json.dumps(themes, ensure_ascii=False))

    def fill_attributes(self, row):
        """填充 Attributes Tab"""
        logger.info("填充 Attributes...")
        click_tab(self.tab, "#/product/attributes")
        time.sleep(3)

        payload = self.build_attributes_payload(row)

        colors = payload.get("colors") or []
        if colors:
            added = self.add_product_colors(colors)
            logger.info(f"  产品颜色: {added}/{len(colors)}")

        materials = payload.get("materials") or []
        if materials:
            try:
                picked = self.select_material_type(materials)
                if picked and picked.get("ok"):
                    logger.info(f"  材质已选择: {picked}")
                else:
                    logger.warning(f"  未匹配到材质: {picked}")
            except Exception as e:
                logger.warning(f"  材质选择失败: {e}")

        size_payload = self.build_product_size_payload(row)
        sz = safe_str(row.get("Size_Values", row.get("Size Values", "")))
        if size_payload.get("sizeCode"):
            result = self.fill_product_size(size_payload)
            if result and result.get("ok"):
                logger.info(f"  产品尺寸已填写: {result}")
            else:
                logger.warning(f"  产品尺寸填写失败: {result}")
        elif sz:
            sz = sz.replace("×", "x").replace(" X ", " x ")
            fill_token_input(self.tab, "txtOtherSizes", sz)

        shape_result = self.fill_attribute_shapes(payload)
        if shape_result and shape_result.get("changed"):
            logger.info(f"  Shape已填写: {shape_result}")

        weight_result = self.fill_attribute_weights(payload.get("weights") or [])
        if weight_result and weight_result.get("changed"):
            logger.info(f"  Item Weight已填写: {weight_result}")

    def fill_attribute_shapes(self, payload):
        """Fill Attributes > Shapes from Shapes/Search Filter/Any Custom columns."""
        shapes = payload.get("shapes") or []
        search_filter = payload.get("shapeSearchFilter") or ""
        options = payload.get("shapeOptions") or []
        if not shapes and not search_filter and not options:
            return None

        if search_filter:
            try:
                self.tab.run_js("""
                    var root = document.getElementById('product-attributes-view') || document.body;
                    var vm = window.ko && ko.dataFor(root);
                    if (!vm) return false;
                    if (typeof vm.shapesSearchFilter === 'function') vm.shapesSearchFilter(arguments[0]);
                    if (typeof vm.onShapesFilterChanged === 'function') vm.onShapesFilterChanged();
                    return true;
                """, search_filter)
            except Exception as e:
                logger.warning(f"  Shape搜索方式设置失败: {e}")

        result = self.tab.run_js("""
            var payload = JSON.parse(arguments[0] || "{}");
            var root = document.getElementById('product-attributes-view') || document.body;
            var vm = window.ko && ko.dataFor(root);
            if (!vm) return {ok:false, error:'attributes vm not found'};

            function unwrap(value) {
                try {
                    if (value && typeof value.peek === 'function') return value.peek();
                    return typeof value === 'function' ? value() : value;
                } catch (e) {
                    return null;
                }
            }
            function norm(value) {
                return String(value == null ? '' : value).trim().toLowerCase();
            }
            function ensureShapeOptionIds() {
                var opts = unwrap(vm.shapesOption) || [];
                var allShapes = unwrap(vm.shapes) || [];
                opts.forEach(function(opt) {
                    var value = norm(unwrap(opt.value));
                    if (opt.setCodeValueId) return;
                    var match = allShapes.find(function(item) {
                        var shapeName = norm(item.Value || unwrap(item.Value));
                        return (value === 'any' && shapeName === 'any shape') ||
                            (value === 'custom' && shapeName.indexOf('custom shape') === 0);
                    });
                    if (match) opt.setCodeValueId = match.Key || unwrap(match.Key);
                });
            }
            function addShapeToken(shapeName) {
                shapeName = String(shapeName || '').trim();
                if (!shapeName) return {shape: shapeName, result: 'blank'};
                var allShapes = unwrap(vm.shapes) || [];
                var wanted = norm(shapeName);
                var match = allShapes.find(function(item) {
                    return norm(item.Value || unwrap(item.Value)) === wanted;
                }) || allShapes.find(function(item) {
                    var itemName = norm(item.Value || unwrap(item.Value));
                    return itemName.indexOf(wanted) >= 0 || wanted.indexOf(itemName) >= 0;
                });
                var token = match
                    ? {Key: match.Key || unwrap(match.Key), Value: match.Value || unwrap(match.Value)}
                    : {Key: shapeName, Value: shapeName, IsValidMatch: true};
                try {
                    if (window.jQuery && jQuery.fn && jQuery.fn.tokenInput && jQuery('#shapesInput').data('tokenInputObject')) {
                        jQuery('#shapesInput').tokenInput('add', token);
                        return {shape: shapeName, result: 'tokenInput', token: token};
                    }
                } catch (e) {
                    return {shape: shapeName, result: 'tokenInput-error', error: String(e)};
                }
                var input = document.getElementById('token-input-shapesInput');
                if (input) {
                    input.focus();
                    input.value = shapeName;
                    input.dispatchEvent(new Event('input', {bubbles:true}));
                    input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', keyCode:13, which:13, bubbles:true}));
                    return {shape: shapeName, result: 'keyboard'};
                }
                return {shape: shapeName, result: 'not-found'};
            }
            function selectOption(name) {
                var opts = unwrap(vm.shapesOption) || [];
                var wanted = norm(name);
                var opt = opts.find(function(item) { return norm(unwrap(item.value)) === wanted; });
                if (!opt) return 'not-found';
                if (!opt.setCodeValueId) return 'missing-id';
                if (typeof opt.isSelected === 'function' && !opt.isSelected()) {
                    opt.isSelected(true);
                    if (typeof vm.shapeOptionSelected === 'function') vm.shapeOptionSelected(opt);
                    return 'selected';
                }
                return 'already';
            }

            if (payload.shapeSearchFilter && typeof vm.shapesSearchFilter === 'function') {
                vm.shapesSearchFilter(payload.shapeSearchFilter);
            }
            ensureShapeOptionIds();
            var tokenResults = [];
            (payload.shapes || []).forEach(function(shape) {
                tokenResults.push(addShapeToken(shape));
            });
            var optionResults = {};
            (payload.shapeOptions || []).forEach(function(option) {
                optionResults[option] = selectOption(option);
            });
            if (typeof vm.hasShapes === 'function' && ((payload.shapes || []).length || (payload.shapeOptions || []).length)) {
                vm.hasShapes(true);
            }
            if (typeof vm.isDirty === 'function') vm.isDirty(true);
            var productInfo = typeof vm.productInfo === 'function' ? vm.productInfo() : null;
            if (productInfo && typeof productInfo.childChanged === 'function') productInfo.childChanged(true);
            return {
                ok: true,
                changed: (payload.shapeSearchFilter ? ['shapeSearchFilter'] : [])
                    .concat((payload.shapes || []).length ? ['shapes'] : [])
                    .concat((payload.shapeOptions || []).length ? ['shapeOptions'] : []),
                addedShapes: payload.shapes || [],
                tokenResults: tokenResults,
                optionResults: optionResults,
                searchFilter: payload.shapeSearchFilter || ''
            };
        """, json.dumps({
            "shapes": shapes,
            "shapeSearchFilter": search_filter,
            "shapeOptions": options,
        }, ensure_ascii=False))
        return result

    def fill_attribute_weights(self, weights):
        """Fill Attributes > Item Weight."""
        if not weights:
            return None
        return self.tab.run_js("""
            var weights = JSON.parse(arguments[0] || "[]");
            var root = document.getElementById('product-attributes-view') || document.body;
            var vm = window.ko && ko.dataFor(root);
            if (!vm) return {ok:false, error:'attributes vm not found'};

            function unwrap(value) {
                try {
                    if (value && typeof value.peek === 'function') return value.peek();
                    return typeof value === 'function' ? value() : value;
                } catch (e) {
                    return null;
                }
            }
            function setObs(target, key, value) {
                if (!target || value === undefined || value === null) return false;
                if (typeof target[key] === 'function') target[key](value);
                else target[key] = value;
                return true;
            }
            function getRows() {
                return unwrap(vm.selectedProductWeights) || [];
            }
            function setHoldValue(row, value, unit) {
                var hold = row && row.holdCSValue;
                var holdValues = hold ? unwrap(hold.value) || [] : [];
                if (!holdValues.length) return;
                holdValues[0].UnitValue = value;
                holdValues[0].UnitOfMeasureCode = unit || holdValues[0].UnitOfMeasureCode || 'POUN';
            }
            function setNativeRow(rowEl, value, unit) {
                if (!rowEl) return;
                var input = rowEl.querySelector('input[type="text"]');
                var select = rowEl.querySelector('select');
                if (select && unit) {
                    select.value = unit;
                    select.dispatchEvent(new Event('change', {bubbles:true}));
                }
                if (input) {
                    var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                    if (setter && setter.set) setter.set.call(input, value);
                    else input.value = value;
                    input.dispatchEvent(new Event('input', {bubbles:true}));
                    input.dispatchEvent(new Event('change', {bubbles:true}));
                    input.dispatchEvent(new Event('blur', {bubbles:true}));
                }
            }

            var rows = getRows();
            while (rows.length < weights.length && typeof vm.addProductWeight === 'function') {
                vm.addProductWeight();
                rows = getRows();
            }

            var changed = [];
            weights.forEach(function(item, index) {
                var row = rows[index];
                if (!row) return;
                var criteriaValue = unwrap(row.criteriaValue) || row.criteriaValue;
                var unit = item.unit || 'POUN';
                setObs(criteriaValue, 'UOM', unit);
                setObs(criteriaValue, 'Value', item.value);
                setHoldValue(row, item.value, unit);
                var rowEl = document.querySelectorAll('#product-attributes-view [data-bind*="criteriaValue"]')[index]
                    || document.querySelectorAll('#product-attributes-view .subsection [data-bind*="selectedProductWeights"] .row-fluid')[index];
                setNativeRow(rowEl, item.value, unit);
                changed.push({value: item.value, unit: unit});
            });

            if (typeof vm.isDirty === 'function') vm.isDirty(true);
            var productInfo = typeof vm.productInfo === 'function' ? vm.productInfo() : null;
            if (productInfo && typeof productInfo.childChanged === 'function') productInfo.childChanged(true);
            document.querySelectorAll('#product-attributes-view input, #product-attributes-view select').forEach(function(el) {
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new Event('blur', {bubbles:true}));
            });
            return {ok:true, changed: changed};
        """, json.dumps(weights, ensure_ascii=False))

    def fill_product_size(self, payload):
        """Fill Attributes > Product Size from parsed Select Size / Size_Values."""
        return self.tab.run_js("""
            var payload = JSON.parse(arguments[0] || "{}");
            var root = document.getElementById('product-attributes-view') || document.body;
            var vm = window.ko && ko.dataFor(root);
            if (!vm) return {ok:false, error:'attributes vm not found'};

            function unwrap(v) {
                try { return typeof v === 'function' ? v() : v; } catch (e) { return null; }
            }
            function setObs(target, key, value) {
                if (!target || value === undefined || value === null) return false;
                if (typeof target[key] === 'function') target[key](value);
                else target[key] = value;
                return true;
            }
            function text(v) { return String(v == null ? '' : v).trim(); }
            function norm(v) {
                return text(v).toLowerCase().replace(/[()"]/g, '').replace(/\\s+/g, ' ');
            }
            function setSelectedType(code) {
                if (!code) return;
                var current = unwrap(vm.selectedSizeType);
                if (current !== code && typeof vm.cmbSizeTypeChanged === 'function') {
                    vm.cmbSizeTypeChanged(code);
                    return;
                }
                setObs(vm, 'selectedSizeType', code);
                if (payload.selectSize) setObs(vm, 'selectedSizeTypeFormatName', payload.selectSize);
            }
            function ensureSizeRows(listName, count) {
                var list = unwrap(vm[listName]) || [];
                while (list.length < count && typeof vm.addNewSize === 'function') {
                    vm.addNewSize(vm);
                    list = unwrap(vm[listName]) || [];
                }
                return list;
            }
            function dimensionId(name) {
                var wanted = norm(name);
                var dims = unwrap(vm.dimensions) || [];
                for (var i = 0; i < dims.length; i++) {
                    var display = unwrap(dims[i].displayName) || unwrap(dims[i].name);
                    if (norm(display) === wanted) return unwrap(dims[i].id) || unwrap(dims[i].criteriaCode);
                }
                var fallback = {length:31, width:32, height:33, dia:35, depth:34, thickness:38, arc:28, area:29, circumference:30};
                return fallback[wanted] || 31;
            }
            function tokenAdd(id, values) {
                var added = [];
                (values || []).forEach(function(value) {
                    value = text(value);
                    if (!value) return;
                    try {
                        if (window.jQuery && jQuery('#' + id).data('tokenInputObject')) {
                            jQuery('#' + id).tokenInput('add', {Key:value, Value:value});
                            added.push(value);
                        } else {
                            var input = document.getElementById('token-input-' + id);
                            if (input) {
                                input.value = value;
                                input.dispatchEvent(new Event('input', {bubbles:true}));
                                input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', keyCode:13, which:13, bubbles:true}));
                                added.push(value);
                            }
                        }
                    } catch (e) {}
                });
                return added;
            }
            function chooseOption(listName, value, fnName, selectedKey) {
                var list = unwrap(vm[listName]) || [];
                var wanted = norm(value);
                var item = list.find(function(x) { return norm(unwrap(x.value) || unwrap(x.displayName) || unwrap(x.description)) === wanted; });
                if (!item) return '';
                if (selectedKey) setObs(item, selectedKey, unwrap(item.value) || value);
                else if (typeof item.isSelected === 'function') item.isSelected(true);
                if (typeof vm[fnName] === 'function') vm[fnName](item);
                return unwrap(item.value) || value;
            }

            var code = payload.sizeCode || '';
            if (!code) return {ok:false, error:'empty size code'};
            setSelectedType(code);
            var result = {ok:true, code:code, changed:[]};

            if (code === 'DIMS') {
                var dims = payload.dimensions || [];
                var rows = ensureSizeRows('dimensionCustomValues', Math.max(1, Math.ceil(dims.length / 3)));
                dims.forEach(function(dim, index) {
                    var row = rows[Math.floor(index / 3)];
                    if (!row) return;
                    var slot = (index % 3) + 1;
                    var estimate = unwrap(row.dimensionEstimate) || row.dimensionEstimate;
                    var attribId = dimensionId(dim.dimension);
                    setObs(estimate, 'dim' + slot + 'AttribId', attribId);
                    setObs(estimate, 'dim' + slot + 'Value', dim.value);
                    setObs(estimate, 'dim' + slot + 'UOM', dim.unit || 'INCH');
                    var hold = unwrap(row.holdCSValue) || row.holdCSValue;
                    var values = hold ? unwrap(hold.value) || [] : [];
                    if (values[slot - 1]) {
                        values[slot - 1].CriteriaAttributeId = attribId;
                        values[slot - 1].UnitValue = dim.value;
                        values[slot - 1].UnitOfMeasureCode = dim.unit || 'INCH';
                    }
                });
                result.changed.push('dimensions');
                result.dimensions = dims;
            } else if (code === 'SOTH') {
                if (payload.option) result.option = chooseOption('otherOption', payload.option, 'chkSelected');
                result.customValues = tokenAdd('txtOtherSizes', payload.customValues || []);
                result.changed.push('other');
            } else if (code === 'SSNM') {
                if (payload.option === 'One Size' || payload.option === 'Adjustable') {
                    result.option = chooseOption('standardNumberedOption', payload.option, 'rbSelected', 'selected');
                } else {
                    result.option = chooseOption('standardNumberedOption', 'Standard', 'rbSelected', 'selected');
                    result.customValues = tokenAdd('txtSSNMCustomSizes', payload.customValues || []);
                }
                result.changed.push('standardNumbered');
            } else if (['MENS','MENT','UNIS','WOMS','WOMT','YOUT','BOYS','GIRL'].indexOf(code) >= 0) {
                if (payload.option === 'OSFA') {
                    result.option = chooseOption('apparelStyleOption', 'OSFA', 'rbApparelStyleSelected', 'selected');
                } else {
                    result.option = chooseOption('apparelStyleOption', 'Standard', 'rbApparelStyleSelected', 'selected');
                    result.customValues = tokenAdd('txtApparelStyleCustomSizes', payload.customValues || []);
                }
                result.changed.push('apparelStyle');
            } else if (code === 'CAPS' || code === 'SVWT') {
                var listName = code === 'CAPS' ? 'capacityCustomValues' : 'volumeCustomValues';
                var values = code === 'CAPS' ? (payload.capacity || []) : (payload.volume || []);
                var rows2 = ensureSizeRows(listName, Math.max(1, values.length));
                values.forEach(function(item, index) {
                    var row = rows2[index];
                    if (!row) return;
                    var criteriaValueObj = unwrap(row.criteriaValue) || row.criteriaValue;
                    if (criteriaValueObj) {
                        if (criteriaValueObj.UOM !== undefined && item.unit) {
                            if (typeof criteriaValueObj.UOM === 'function') criteriaValueObj.UOM(item.unit);
                            else criteriaValueObj.UOM = item.unit;
                        }
                        if (criteriaValueObj.Value !== undefined) {
                            if (typeof criteriaValueObj.Value === 'function') criteriaValueObj.Value(item.value);
                            else criteriaValueObj.Value = item.value;
                        }
                    }
                });
                result.changed.push(code === 'CAPS' ? 'capacity' : 'volume');
            } else if (['SABR','SAHU','SAIT'].indexOf(code) >= 0) {
                result.customValues = tokenAdd('txtApparelStyleCustomSizes', payload.customValues || []);
                result.changed.push('apparelCustom');
            } else if (code === 'SANS' || code === 'SAWI') {
                result.apparelValues = payload.apparelValues || [];
                result.customValues = tokenAdd('txtApparelStyleCustomSizes', (payload.apparelValues || []).map(function(x) {
                    return x.name + '=' + x.value;
                }));
                result.changed.push('apparelStructured');
            }

            if (typeof vm.isDirty === 'function') vm.isDirty(true);
            document.querySelectorAll('#product-attributes-view input, #product-attributes-view select, #product-attributes-view textarea').forEach(function(el) {
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new Event('blur', {bubbles:true}));
            });
            return result;
        """, json.dumps(payload, ensure_ascii=False))

    def select_material_type(self, material_entries):
        """Select Attributes > Product Materials through the page ViewModel.
        Supports ASI material aliases and falls back to Other for custom names.
        """
        if isinstance(material_entries, str):
            entries = [
                {"material": name, "alias": name}
                for name in self.split_excel_multi(material_entries, seps=(",", ";"))
            ]
        else:
            entries = []
            for entry in material_entries or []:
                if isinstance(entry, dict):
                    material = safe_str(entry.get("material", ""))
                    alias = safe_str(entry.get("alias", "")) or material
                else:
                    material = safe_str(entry)
                    alias = material
                if material:
                    entries.append({"material": material, "alias": alias})
        if not entries:
            return {"ok": False, "error": "no material entries"}

        results = []
        for idx, entry in enumerate(entries):
            wanted = entry["material"]
            alias = entry["alias"]
            result = self.tab.run_js("""
                var wanted = String(arguments[0] || '').trim();
                var alias = String(arguments[1] || wanted).trim();
                var setIndex = Number(arguments[2] || 0);
                function unwrap(value) {
                    try {
                        if (!value) return value;
                        if (typeof value.peek === 'function') return value.peek();
                        if (typeof value === 'function') return value();
                        return value;
                    } catch (e) {
                        return null;
                    }
                }
                function nameOf(item) {
                    return String(unwrap(item && item.displayName) || unwrap(item && item.display) || unwrap(item && item.description) || '').trim();
                }
                function flattenGroups(groups) {
                    var out = [];
                    (groups || []).forEach(function(group) {
                        var children = unwrap(group.majorCodeValueGroups) || [];
                        children.forEach(function(item) { out.push(item); });
                    });
                    return out;
                }
                var rootEl = document.getElementById('product-attributes-view') || document.body;
                var vm = window.ko && ko.dataFor(rootEl);
                if (!vm) return {ok:false, error:'attributes vm not found'};

                var selectedSets = unwrap(vm.selectedMaterialTypes) || [];
                while (selectedSets.length <= setIndex && typeof vm.addAdditionalMaterial === 'function') {
                    vm.addAdditionalMaterial();
                    selectedSets = unwrap(vm.selectedMaterialTypes) || [];
                }
                var targetSet = selectedSets[setIndex];
                if (!targetSet) return {ok:false, error:'material set ' + setIndex + ' not found'};
                var combos = unwrap(targetSet.materialTypes) || [];
                var combo = combos[0];
                if (!combo) return {ok:false, error:'material combo not found in set ' + setIndex};

                if (typeof vm.showMaterialTypePop === 'function') vm.showMaterialTypePop(combo);
                if (typeof vm.showAllMaterials === 'function') vm.showAllMaterials();
                if (typeof vm.filterMaterialName === 'function') vm.filterMaterialName(wanted);

                var groups = [];
                if (typeof vm.getFilteredMaterials === 'function') groups = unwrap(vm.getFilteredMaterials) || [];
                var candidates = flattenGroups(groups);
                if (!candidates.length && typeof vm.filterMaterialName === 'function') {
                    vm.filterMaterialName('');
                    groups = typeof vm.getFilteredMaterials === 'function' ? (unwrap(vm.getFilteredMaterials) || []) : [];
                    candidates = flattenGroups(groups);
                }

                var target = wanted.toLowerCase();
                var match = candidates.find(function(item) { return nameOf(item).toLowerCase() === target; })
                    || candidates.find(function(item) {
                        var name = nameOf(item).toLowerCase();
                        return name.indexOf(target) >= 0 || target.indexOf(name) >= 0;
                    });
                var fallback = false;
                if (!match) {
                    match = candidates.find(function(item) { return nameOf(item).toLowerCase() === 'other'; });
                    fallback = !!match;
                }
                if (!match) return {ok:false, error:'material and Other fallback not found', wanted:wanted, samples:candidates.slice(0, 12).map(nameOf)};

                if (typeof vm.selectMaterialType === 'function') {
                    vm.selectMaterialType(match);
                } else {
                    combo.materialType(match);
                }
                if (window.jQuery) jQuery('#materialTypeModal').modal('hide');

                if (alias && typeof targetSet.aliasName === 'function') {
                    targetSet.aliasName(alias);
                    if (typeof targetSet.materialTypeTemplate === 'function') targetSet.materialTypeTemplate('edit');
                    if (typeof targetSet.materialTypeTemplateLink === 'function') targetSet.materialTypeTemplateLink('Save');
                    if (typeof vm.toggleMaterialTypeTemplate === 'function') {
                        vm.toggleMaterialTypeTemplate(targetSet);
                    }
                }

                var selectedName = '';
                try {
                    selectedName = alias || nameOf(combo.materialType());
                } catch (e) {}
                if (typeof vm.isDirty === 'function') vm.isDirty(true);
                document.querySelectorAll('#materialTypesParentDiv input').forEach(function(el) {
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                    el.dispatchEvent(new Event('blur', {bubbles:true}));
                });
                document.querySelectorAll('#materialTypeModal').forEach(function(el) {
                    el.classList.remove('in');
                    el.style.display = 'none';
                    el.setAttribute('aria-hidden', 'true');
                });
                document.querySelectorAll('.modal-backdrop').forEach(function(el) { el.remove(); });
                document.body.classList.remove('modal-open');
                return {ok:true, wanted:wanted, alias:alias, matched:nameOf(match), selected:selectedName, fallback:fallback};
            """, wanted, alias, idx)
            results.append(result)
            if not result or not result.get("ok"):
                break

        ok_count = sum(1 for r in results if r and r.get("ok"))
        return {
            "ok": ok_count == len(entries) and len(entries) > 0,
            "wanted": [entry["material"] for entry in entries],
            "aliases": [entry["alias"] for entry in entries],
            "matched": [r.get("matched") for r in results if r],
            "selected": [r.get("selected") for r in results if r],
            "fallbacks": [r.get("fallback", False) for r in results if r],
            "results": results,
        }

    def add_product_colors(self, colors):
        """Click ASI standard color buttons by color alias."""
        added = 0
        for target in AsiBot.parse_product_colors(",".join(colors)):
            try:
                ok = self.tab.run_js("""
                    var target = String(arguments[0] || '').trim().toLowerCase();
                    function text(el) {
                        return String((el && (el.textContent || el.innerText)) || '').replace(/\\s+/g, ' ').trim();
                    }
                    var selected = Array.from(document.querySelectorAll('#product-attributes-view *'))
                        .map(text)
                        .filter(function(t) { return /^#\\d+:/i.test(t); })
                        .map(function(t) { return t.replace(/^#\\d+:\\s*/i, '').trim().toLowerCase(); });
                    if (selected.indexOf(target) >= 0) {
                        return 'exists';
                    }
                    var buttons = Array.from(document.querySelectorAll('#product-attributes-view button.button-color'));
                    for (var i = 0; i < buttons.length; i++) {
                        var text = (buttons[i].textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                        if (text === target) {
                            buttons[i].click();
                            return 'clicked';
                        }
                    }
                    return 'not_found';
                """, target)
                if ok == "clicked" or ok is True:
                    added += 1
                    time.sleep(0.2)
                elif ok == "exists":
                    logger.info(f"  颜色已存在，跳过: {target}")
                else:
                    logger.warning(f"  未找到颜色按钮: {target}")
            except Exception as e:
                logger.warning(f"  颜色选择失败 [{target}]: {e}")
        return added

    def fill_imprint(self, row, upload_controls=None, product_flags=None):
        """填充 Imprint Tab"""
        logger.info("填充 Imprint...")
        click_tab(self.tab, "#/product/imprint")
        time.sleep(3)

        payload = self.build_imprint_payload(row)
        if field_mode(upload_controls, "imprint", "unimprinted") != MODE_SKIP:
            unimprinted_result = self.tab.run_js("""
            var desired = Boolean(arguments[0]);
            var root = document.getElementById("product-imprint-view");
            var vm = root && window.ko ? ko.dataFor(root) : null;
            function unwrap(v) { return typeof v === "function" ? v() : v; }
            function norm(value) {
                return String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();
            }
            function setDirty() {
                if (vm && typeof vm.isDirty === "function") vm.isDirty(true);
            }
            var inputs = Array.from(document.querySelectorAll("#product-imprint-view input[type='checkbox']"));
            var input = inputs.find(function(item) {
                var container = item.closest("label") || item.closest(".checkbox") || item.parentElement;
                return container && norm(container.textContent).indexOf("unimprinted") >= 0;
            });
            var changed = false;
            if (input && !!input.checked !== desired) {
                input.click();
                changed = true;
            }
            ["isSoldUnimprinted", "isUnimprinted", "isUnimprintedAvailable", "unimprinted"].forEach(function(key) {
                if (vm && typeof vm[key] === "function" && !!unwrap(vm[key]) !== desired) {
                    try {
                        vm[key](desired);
                        changed = true;
                    } catch (e) {}
                }
            });
            if (changed) setDirty();
            return {ok: !!input || changed, changed: changed, checked: input ? !!input.checked : null};
            """, (
                normalize_product_flags(product_flags)["unimprinted"]
                if product_flags is not None
                else runtime_switch_value(upload_controls, "imprint", "unimprinted")
            ))
            logger.info(f"  Unimprinted默认勾选: {unimprinted_result}")
        if payload["methods"]:
            for method in payload["methods"]:
                result = self.tab.run_js("""
                    var method = String(arguments[0] || "").trim().toLowerCase();
                    var root = document.getElementById("product-imprint-view");
                    var vm = root && window.ko ? ko.dataFor(root) : null;
                    function unwrap(v) { return typeof v === "function" ? v() : v; }
                    function textOf(item) {
                        if (!item) return "";
                        return String(unwrap(item.codeValue) || unwrap(item.displayName) || unwrap(item.name) || unwrap(item.value) || item.codeValue || item.displayName || "").trim();
                    }
                    var existing = vm && vm.selectedImprintMethods ? unwrap(vm.selectedImprintMethods) || [] : [];
                    if (existing.some(function(x) { return textOf(x).toLowerCase() === method; })) return "exists";

                    var sel = document.getElementById("cmbImprintMethods");
                    if (!sel) return "select-not-found";
                    var option = Array.from(sel.options).find(function(opt) {
                        return (opt.textContent || "").trim().toLowerCase() === method;
                    }) || Array.from(sel.options).find(function(opt) {
                        var txt = (opt.textContent || "").trim().toLowerCase();
                        return txt.indexOf(method) >= 0 || method.indexOf(txt) >= 0;
                    });
                    if (!option) return "option-not-found";
                    sel.value = option.value;
                    sel.dispatchEvent(new Event("change", {bubbles:true}));
                    if (vm && typeof vm.selectedImprintMethod === "function") {
                        var all = unwrap(vm.imprintMethods) || [];
                        var item = all.find(function(x) { return textOf(x).toLowerCase() === (option.textContent || "").trim().toLowerCase(); });
                        if (item) vm.selectedImprintMethod(item);
                    }
                    var addBtn = Array.from(document.querySelectorAll('#product-imprint-view button')).find(function(btn) {
                        return /^Add$/.test((btn.textContent || "").trim()) && !btn.disabled;
                    });
                    if (addBtn) {
                        addBtn.click();
                        return "clicked";
                    }
                    if (vm && typeof vm.addImprintMethod === "function") {
                        vm.addImprintMethod();
                        return "model";
                    }
                    return "add-not-found";
                """, method)
                logger.info(f"  印刷方式 {method}: {result}")
                time.sleep(0.8)

        if (
            payload["personalizationMethods"]
            or payload["hasImprintColorCodes"]
            or payload["colors"]
            or payload["rushService"]
            or payload["sameDayService"]
            or payload["additionalColorsInfo"]
            or payload["artwork"]
            or payload["productionTime"]
            or payload["imprintSize"]
            or payload["imprintLocation"]
            or payload["imprintLocations"]
        ):
            result = self.tab.run_js("""
                try {
                    var payload = JSON.parse(arguments[0] || "{}");
                    var root = document.getElementById("product-imprint-view");
                    var vm = root && window.ko ? ko.dataFor(root) : null;
                    if (!vm) return {ok: false, error: "product-imprint-view ko model not found"};
                    function unwrap(v) { return typeof v === "function" ? v() : v; }
                    function setObs(target, key, value) {
                        if (!target || value === undefined || value === null || value === "") return false;
                        if (typeof target[key] === "function") target[key](value);
                        else target[key] = value;
                        return true;
                    }
                    function textOf(item) {
                        if (!item) return "";
                        return String(unwrap(item.codeValue) || unwrap(item.displayName) || unwrap(item.name) || unwrap(item.value) || item.codeValue || item.displayName || "").trim();
                    }
                    function norm(value) {
                        return String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                    }
                    function listOf(name) {
                        return vm && vm[name] ? unwrap(vm[name]) || [] : [];
                    }
                    function callMaybe(name, arg) {
                        if (vm && typeof vm[name] === "function") {
                            try {
                                if (arguments.length > 1) vm[name](arg);
                                else vm[name]();
                                return true;
                            } catch (e) {}
                        }
                        return false;
                    }
                    function setDirty() {
                        if (vm && typeof vm.isDirty === "function") vm.isDirty(true);
                    }
                    function checkboxByText(label) {
                        var target = norm(label);
                        var inputs = Array.from(document.querySelectorAll("#product-imprint-view input[type='checkbox']"));
                        return inputs.find(function(input) {
                            var container = input.closest(".checkbox") || input.parentElement || input.closest(".controls-row");
                            return container && norm(container.textContent).indexOf(target) >= 0;
                        }) || null;
                    }
                    function ensureCheckbox(label, desired) {
                        var input = checkboxByText(label);
                        if (input && !!input.checked !== !!desired) {
                            input.click();
                            return "clicked";
                        }
                        return input ? "ok" : "not-found";
                    }
                    function optionMatch(raw, candidate) {
                        var a = norm(raw);
                        var b = norm(candidate);
                        return a === b || a.indexOf(b) >= 0 || b.indexOf(a) >= 0;
                    }
                    function firstOrAdd(listName, addFnName, code) {
                        var list = listOf(listName);
                        if (!list.length && vm && typeof vm[addFnName] === "function") {
                            if (code) vm[addFnName](code);
                            else vm[addFnName]();
                            list = listOf(listName);
                        }
                        return list[0] || null;
                    }
                    function itemAtOrAdd(listName, addFnName, code, index) {
                        var list = listOf(listName);
                        var guard = 0;
                        while (list.length <= index && guard < 10 && vm && typeof vm[addFnName] === "function") {
                            if (code) vm[addFnName](code);
                            else vm[addFnName]();
                            list = listOf(listName);
                            guard += 1;
                        }
                        return list[index] || null;
                    }
                    function productionValue(raw) {
                        raw = String(raw || "").trim();
                        var m = raw.match(/\\d+(?:\\s*-\\s*\\d+)?/);
                        return m ? m[0].replace(/\\s+/g, "") : raw;
                    }
                    function addPersonalization(method) {
                        var existing = listOf("selectedPersonalizationMethods");
                        if (existing.some(function(item) { return optionMatch(method, textOf(item)); })) return "exists";

                        if (typeof vm.isPersonalizationAvailable === "function" && !unwrap(vm.isPersonalizationAvailable)) {
                            var clicked = ensureCheckbox("Personalization Available", true);
                            if (clicked === "not-found") {
                                vm.isPersonalizationAvailable(true);
                                callMaybe("chkPersonalizationSelected");
                            }
                        }

                        var all = vm.personalizationMethods ? unwrap(vm.personalizationMethods) || [] : [];
                        var item = all.find(function(candidate) { return optionMatch(method, textOf(candidate)); });
                        var sel = document.getElementById("cmbPersonalizationMethods");
                        var opt = sel && Array.from(sel.options).find(function(option) { return optionMatch(method, option.textContent); });
                        if (!item && !opt) return "option-not-found";
                        if (item && typeof vm.selectedPersonalizationMethod === "function") {
                            vm.selectedPersonalizationMethod(item);
                            if (typeof vm.addPersonalizationMethod === "function") {
                                vm.addPersonalizationMethod();
                                return "model";
                            }
                        }
                        if (sel) {
                            if (opt) {
                                sel.selectedIndex = Array.from(sel.options).indexOf(opt);
                                sel.dispatchEvent(new Event("change", {bubbles:true}));
                            }
                        }
                        var addBtn = sel && Array.from(sel.parentElement.querySelectorAll("button")).find(function(btn) {
                            return /^Add$/.test((btn.textContent || "").trim()) && !btn.disabled;
                        });
                        if (addBtn) {
                            addBtn.click();
                            return "clicked";
                        }
                        if (typeof vm.addPersonalizationMethod === "function") {
                            vm.addPersonalizationMethod();
                            return "model";
                        }
                        return "selected-no-add";
                    }
                    function setImprintColorFlag(label, obsName, handlerName, desired) {
                        var current = vm && typeof vm[obsName] === "function" ? !!unwrap(vm[obsName]) : false;
                        var input = checkboxByText(label);
                        if (current === !!desired && (!input || !!input.checked === !!desired)) return "ok";
                        if (typeof vm[obsName] === "function") {
                            vm[obsName](!!desired);
                            callMaybe(handlerName);
                            input = checkboxByText(label);
                            if (input && !!input.checked !== !!desired) {
                                input.click();
                                return "model+clicked";
                            }
                            return "model";
                        }
                        var clicked = ensureCheckbox(label, desired);
                        if (clicked === "clicked") return "clicked";
                        return "not-found";
                    }
                    function setTextInputElement(el, value) {
                        if (!el || !value) return false;
                        el.disabled = false;
                        el.value = value;
                        el.dispatchEvent(new Event("input", {bubbles:true}));
                        el.dispatchEvent(new Event("change", {bubbles:true}));
                        el.dispatchEvent(new Event("blur", {bubbles:true}));
                        return true;
                    }
                    function setTextInput(selector, value) {
                        var el = document.querySelector(selector);
                        return setTextInputElement(el, value);
                    }
                    function setFirstTextFields(item, value) {
                        if (!item || !value) return false;
                        var keys = ["details", "value", "description", "comments", "additionalColor", "timeRequired"];
                        var changed = false;
                        keys.forEach(function(key) {
                            if (item[key] !== undefined) changed = setObs(item, key, value) || changed;
                        });
                        return changed;
                    }
                    function setArtwork(name, desired) {
                        var list = vm.availableArtworkAndProofs ? unwrap(vm.availableArtworkAndProofs) || [] : [];
                        var item = list.find(function(candidate) { return optionMatch(name, textOf(candidate)); });
                        if (item && typeof item.isSelected === "function" && !!unwrap(item.isSelected) !== !!desired) {
                            item.isSelected(!!desired);
                            if (typeof vm.selectAvailableArtwork === "function") {
                                try { vm.selectAvailableArtwork(item); } catch (e) {}
                            }
                            return "model";
                        }
                        var input = checkboxByText(name);
                        if (input && !!input.checked !== !!desired) {
                            input.click();
                            return "clicked";
                        }
                        return item || input ? "ok" : "not-found";
                    }
                    function findArtworkItem(name) {
                        var list = vm.availableArtworkAndProofs ? unwrap(vm.availableArtworkAndProofs) || [] : [];
                        return list.find(function(candidate) { return optionMatch(name, textOf(candidate)); }) || null;
                    }
                    function setArtworkComment(name, comment) {
                        if (!comment) return "blank";
                        var item = findArtworkItem(name);
                        var modelChanged = false;
                        if (item) {
                            ["comment", "comments", "artworkComment", "proofComment", "details", "value", "description"].forEach(function(key) {
                                if (item[key] !== undefined) modelChanged = setObs(item, key, comment) || modelChanged;
                            });
                        }
                        var input = checkboxByText(name);
                        if (input) {
                            var containers = [
                                input.closest(".controls-row"),
                                input.closest(".control-group"),
                                input.closest(".row-fluid"),
                                input.parentElement,
                                input.parentElement && input.parentElement.parentElement
                            ].filter(Boolean);
                            for (var c = 0; c < containers.length; c++) {
                                var field = Array.from(containers[c].querySelectorAll("input:not([type='checkbox']):not([type='radio']):not([type='file']), textarea")).find(function(el) {
                                    return !el.disabled && (el.offsetParent || el.getClientRects().length);
                                });
                                if (field && setTextInputElement(field, comment)) return modelChanged ? "model+dom" : "dom";
                            }
                        }
                        return modelChanged ? "model" : "not-found";
                    }
                    var changed = [];
                    var personalizationResults = [];
                    if (!payload.hasPersonalizationAvailable) {
                        if (typeof vm.isPersonalizationAvailable === "function" && unwrap(vm.isPersonalizationAvailable)) {
                            vm.isPersonalizationAvailable(false);
                            callMaybe("chkPersonalizationSelected");
                            changed.push("personalization-cleared");
                        }
                        var personalizationCheckbox = checkboxByText("Personalization Available");
                        if (personalizationCheckbox && personalizationCheckbox.checked) {
                            personalizationCheckbox.click();
                            changed.push("personalization-unchecked");
                        }
                    }
                    (payload.personalizationMethods || []).forEach(function(method) {
                        var result = addPersonalization(method);
                        personalizationResults.push({method: method, result: result});
                        changed.push("personalization");
                    });

                    var colorFlagResults = {};
                    if (payload.hasImprintColorCodes) {
                        colorFlagResults.standard = setImprintColorFlag("Standard Colors", "isStandardColors_Checked", "chkStandardColors_Selected", !!payload.imprintColors.standard);
                        colorFlagResults.custom = setImprintColorFlag("Custom Colors", "isCustomColors_Checked", "chkCustomColors_Selected", !!payload.imprintColors.custom);
                        colorFlagResults.pms = setImprintColorFlag("PMS Colors", "isPMSColors_Checked", "chkPMSColors_Selected", !!payload.imprintColors.pms);
                        changed.push("imprintColors");
                    }
                    if ((payload.colors || []).length) {
                        setObs(vm, "imprintColorBy", "COLR");
                        var joinedColors = payload.colors.join(", ");
                        var tokenResults = [];
                        if (window.jQuery && jQuery.fn && jQuery.fn.tokenInput) {
                            (payload.colorTokens || []).forEach(function(item) {
                                try {
                                    jQuery("#imprintColorNames").tokenInput("add", item);
                                    tokenResults.push(item.name);
                                } catch (e) {
                                    tokenResults.push("error:" + item.name + ":" + e);
                                }
                            });
                        }
                        setTextInput("#imprintColorNames", joinedColors);
                        changed.push("colorTokens:" + tokenResults.join("|"));
                        changed.push("colors");
                    }

                    if (payload.rushService) {
                        if (typeof vm.isRushServiceOffered === "function" && !unwrap(vm.isRushServiceOffered)) {
                            var clickedRush = ensureCheckbox("Rush Service Offered", true);
                            if (clickedRush === "not-found") {
                                vm.isRushServiceOffered(true);
                                callMaybe("chkRushServiceOfferedSelected");
                            }
                        }
                        var rush = firstOrAdd("selectedRushTime", "addRushTime");
                        if (rush && setObs(rush, "timeRequired", productionValue(payload.rushService))) changed.push("rushService");
                        if (rush) setObs(rush, "timeUnit", "BDAY");
                    }
                    if (payload.sameDayService) {
                        ["isSameDayOffered", "isSameDayServiceOffered"].forEach(function(key) {
                            if (typeof vm[key] === "function") vm[key](true);
                        });
                        var same = firstOrAdd("selectedSameDayService", "addSameDayService");
                        if (same && !/^yes|true|1$/i.test(String(payload.sameDayService).trim())) {
                            setFirstTextFields(same, payload.sameDayService);
                        }
                        changed.push("sameDayService");
                    }

                    if (payload.productionTime) {
                        var prod = firstOrAdd("selectedProductionTime", "addProductionTime");
                        if (prod && setObs(prod, "timeRequired", productionValue(payload.productionTime))) changed.push("productionTime");
                        if (prod) setObs(prod, "timeUnit", "BDAY");
                    }
                    if (payload.imprintSize) {
                        var size = firstOrAdd("selectedImprintSize", "addImprintSizeLocation", "IMSZ");
                        if (size && setObs(size, "value", payload.imprintSize)) changed.push("imprintSize");
                    }
                    var imprintLocations = (payload.imprintLocations || []).filter(function(value) {
                        return String(value || "").trim();
                    });
                    if (!imprintLocations.length && payload.imprintLocation) imprintLocations = [payload.imprintLocation];
                    imprintLocations.forEach(function(location, index) {
                        var loc = itemAtOrAdd("selectedImprintLocation", "addImprintSizeLocation", "IMLO", index);
                        if (loc && setObs(loc, "value", location)) changed.push("imprintLocation:" + location);
                    });
                    if (payload.additionalColorsInfo) {
                        var addColor = firstOrAdd("selectedAdditionalColors", "addAdditionalColor");
                        if (setFirstTextFields(addColor, payload.additionalColorsInfo)) changed.push("additionalColorsInfo");
                    }

                    var artworkResults = [];
                    (payload.artwork || []).forEach(function(name) {
                        var result = setArtwork(name, true);
                        var comment = payload.artworkComments ? payload.artworkComments[name] : "";
                        var commentResult = setArtworkComment(name, comment);
                        artworkResults.push({name: name, result: result, comment: comment, commentResult: commentResult});
                        changed.push("artwork");
                        if (comment) changed.push("artworkComment");
                    });
                    setDirty();

                    [
                        "input:not([type='file'])",
                        "select",
                        "textarea"
                    ].forEach(function(selector) {
                        document.querySelectorAll("#product-imprint-view " + selector).forEach(function(el) {
                            if (el.disabled || !(el.offsetParent || el.getClientRects().length)) return;
                            el.dispatchEvent(new Event("input", {bubbles:true}));
                            el.dispatchEvent(new Event("change", {bubbles:true}));
                            el.dispatchEvent(new Event("blur", {bubbles:true}));
                        });
                    });
                    return {
                        ok: true,
                        changed: changed,
                        personalization: personalizationResults,
                        colorFlags: colorFlagResults,
                        productionTime: vm && vm.selectedProductionTime ? (unwrap(vm.selectedProductionTime) || []).map(function(x) { return unwrap(x.timeRequired); }) : [],
                        rushTime: vm && vm.selectedRushTime ? (unwrap(vm.selectedRushTime) || []).map(function(x) { return unwrap(x.timeRequired); }) : [],
                        imprintSize: vm && vm.selectedImprintSize ? (unwrap(vm.selectedImprintSize) || []).map(function(x) { return unwrap(x.value); }) : [],
                        imprintLocation: vm && vm.selectedImprintLocation ? (unwrap(vm.selectedImprintLocation) || []).map(function(x) { return unwrap(x.value); }) : [],
                        imprintColorHiddenValue: (document.getElementById("imprintColorNames") || {}).value || "",
                        selectedAdditionalColors: vm && vm.selectedAdditionalColors ? (unwrap(vm.selectedAdditionalColors) || []).map(function(x) { return unwrap(x.color); }) : [],
                        artwork: vm && vm.availableArtworkAndProofs ? (unwrap(vm.availableArtworkAndProofs) || []).filter(function(x) { return !!unwrap(x.isSelected); }).map(function(x) { return textOf(x); }) : [],
                        colorsInput: (document.getElementById("imprintColorNames") || {}).value || ""
                    };
                } catch (e) {
                    return {ok: false, error: String(e && e.stack || e)};
                }
            """, json.dumps(payload, ensure_ascii=False))
            logger.info(f"  Imprint详情结果: {result}")

        # Imprint Size
        # Setup Charge
        sc = safe_str(row.get("Se-tup Change", ""))
        if sc:
            try:
                self.tab.run_js("""
                    var inputs = document.querySelectorAll('#product-imprint-view input');
                    for (var i=0; i<inputs.length; i++) {
                        var lbl = (inputs[i].closest('.control-group')||{}).textContent||'';
                        if (lbl.indexOf('Setup')!==-1 || lbl.indexOf('Charge')!==-1) {
                            inputs[i].value = arguments[0];
                            inputs[i].dispatchEvent(new Event('change',{bubbles:true}));
                            return true;
                        }
                    }
                    return false;
                """, sc)
                logger.info(f"  安装费: {sc}")
            except Exception as e:
                logger.warning(f"  安装费失败: {e}")

    def fill_pricing(self, row, upload_controls=None):
        """填充 Pricing Tab"""
        logger.info("填充 Pricing...")
        click_tab(self.tab, "#/product/pricing")
        time.sleep(4)

        payload = self.build_pricing_payload(row)
        payload["canOrderLessThanMinimum"] = runtime_switch_value(
            upload_controls, "pricing", "order_less_than_minimum"
        )
        qties = [item["quantity"] for item in payload["prices"]]
        prcs = [item["netCost"] for item in payload["prices"]]

        if not qties:
            logger.warning("  无价格数据")
            return

        logger.info(f"  价格档位: {len(qties)}")
        price_includes = payload.get("priceIncludes", "")
        price_code_value = payload.get("priceCodeValue", "")
        can_order_less_than_minimum = bool(payload.get("canOrderLessThanMinimum", True))
        apply_order_less_than_minimum = (
            field_mode(upload_controls, "pricing", "order_less_than_minimum") != MODE_SKIP
        )
        result = self.tab.run_js("""
            var quantities = JSON.parse(arguments[0] || "[]");
            var netCosts = JSON.parse(arguments[1] || "[]");
            var priceIncludes = arguments[2] || "";
            var priceCodeValue = arguments[3] || "";
            var canOrderLessThanMinimum = !!arguments[4];
            var applyOrderLessThanMinimum = !!arguments[5];

            function isObs(fn) {
                return typeof fn === "function" && typeof fn.subscribe === "function";
            }
            function setObs(target, name, value) {
                if (target && isObs(target[name])) target[name](value);
            }
            function eventValue(el, value) {
                if (!el) return;
                var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
                if (setter && setter.set) setter.set.call(el, value);
                else el.value = value;
                el.focus();
                if (typeof el.select === "function") el.select();
                el.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: String(value).slice(-1) || "0" }));
                el.dispatchEvent(new Event("input", { bubbles: true }));
                el.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, key: String(value).slice(-1) || "0" }));
                el.dispatchEvent(new Event("change", { bubbles: true }));
                el.dispatchEvent(new Event("blur", { bubbles: true }));
            }
            function isVisible(el) {
                return !!(el && (el.offsetParent || el.getClientRects().length));
            }
            function checkboxByText(label) {
                var target = String(label || "").trim().toLowerCase();
                return Array.from(document.querySelectorAll('#product-pricing-view input[type="checkbox"], body input[type="checkbox"]')).find(function(input) {
                    var container = input.closest("label,.checkbox,.control-group,.controls-row,li,div") || input.parentElement;
                    var text = (container && container.textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
                    return text.indexOf(target) >= 0 && isVisible(input);
                }) || null;
            }
            function setCheckbox(input, desired) {
                if (!input) return false;
                if (!!input.checked !== !!desired) input.click();
                input.dispatchEvent(new Event("change", { bubbles: true }));
                input.dispatchEvent(new Event("blur", { bubbles: true }));
                return true;
            }
            function firstBaseGrid(vm) {
                var grids = vm && vm.priceGridCollection && vm.priceGridCollection();
                if (!grids || !grids.length) return null;
                for (var i = 0; i < grids.length; i++) {
                    if (!grids[i].isBasePrice || grids[i].isBasePrice()) return grids[i];
                }
                return grids[0];
            }
            function unwrap(value) {
                try {
                    if (value && typeof value.peek === "function") return value.peek();
                    return typeof value === "function" ? value() : value;
                } catch (e) {
                    return null;
                }
            }
            function setDiscount(price, code, applyToAll) {
                if (!price || !code) return false;
                var discountRate = unwrap(price.discountRate) || price.discountRate;
                if (discountRate) {
                    if (isObs(discountRate.industryDiscountCode)) discountRate.industryDiscountCode(code);
                    else discountRate.industryDiscountCode = code;
                    if (isObs(discountRate.displayValue)) {
                        var current = String(discountRate.displayValue() || "");
                        if (!current || current.indexOf(code) < 0) discountRate.displayValue(code);
                    }
                }
                ["discountCode", "discountRateCode", "industryDiscountCode"].forEach(function (name) {
                    setObs(price, name, code);
                });
                if (isObs(price.isApplyOneCodeEnabled)) price.isApplyOneCodeEnabled(!!applyToAll);
                return true;
            }
            function firstCurrencyGrid(grid) {
                if (!grid || !grid.currencyGrids) return null;
                var cgs = grid.currencyGrids();
                if (!cgs || !cgs.length) return null;
                for (var i = 0; i < cgs.length; i++) {
                    if ((cgs[i].currency || "").toString().toUpperCase() === "USD") return cgs[i];
                }
                return cgs[0];
            }
            function fillPriceArray(prices) {
                if (!prices) return 0;
                var count = Math.min(quantities.length, netCosts.length, prices.length);
                for (var i = 0; i < prices.length; i++) {
                    var active = i < count;
                    if (isObs(prices[i].isEnabled)) prices[i].isEnabled(active);
                    if (active) {
                        setObs(prices[i], "quantity", quantities[i]);
                        setObs(prices[i], "netCost", netCosts[i]);
                        setObs(prices[i], "listPrice", "");
                        setDiscount(prices[i], priceCodeValue, i === 0);
                        if (isObs(prices[i].isQUR)) prices[i].isQUR(false);
                    } else {
                        setObs(prices[i], "quantity", "");
                        setObs(prices[i], "netCost", "");
                        setObs(prices[i], "listPrice", "");
                    }
                }
                return count;
            }

            var root = document.getElementById("product-pricing-view");
            var vm = root && window.ko ? ko.dataFor(root) : null;
            var modelFilled = 0;
            if (vm) {
                if (isObs(vm.defaultPriceType) && vm.defaultPriceType() !== "N") {
                    vm.defaultPriceType("N");
                    if (typeof vm.onChangePriceType === "function") vm.onChangePriceType();
                }
                if (vm.productInfo && vm.productInfo() && isObs(vm.productInfo().costTypeCode)) {
                    vm.productInfo().costTypeCode("N");
                }
                if (applyOrderLessThanMinimum) {
                    [
                        "canOrderLessThanMinimum",
                        "canOrderLessThanMin",
                        "isOrderLessThanMinimum",
                        "allowOrderLessThanMinimum",
                        "orderLessThanMinimum"
                    ].forEach(function(name) {
                        if (isObs(vm[name])) vm[name](canOrderLessThanMinimum);
                        if (vm.productInfo && vm.productInfo() && isObs(vm.productInfo()[name])) vm.productInfo()[name](canOrderLessThanMinimum);
                    });
                }
                var grid = firstBaseGrid(vm);
                if (grid) {
                    setObs(grid, "priceIncludes", priceIncludes);
                    setObs(grid, "isQUR", false);
                    modelFilled = Math.max(modelFilled, fillPriceArray(grid.prices));
                    var currencyGrid = firstCurrencyGrid(grid);
                    if (currencyGrid) {
                        if (isObs(currencyGrid.isQUR)) currencyGrid.isQUR(false);
                        modelFilled = Math.max(modelFilled, fillPriceArray(currencyGrid.prices));
                    }
                }
                if (isObs(vm.showPriceTypeChangeWarningModal)) vm.showPriceTypeChangeWarningModal(false);
                if (isObs(vm.isDirty)) vm.isDirty(true);
            }

            var priceTypeSelect = Array.from(document.querySelectorAll(
                '#product-pricing-view select'
            )).find(function (s) {
                return Array.from(s.options || []).some(function (o) { return o.value === "N"; }) &&
                    /defaultPriceType/.test(s.getAttribute("data-bind") || "");
            });
            if (priceTypeSelect) priceTypeSelect.value = "N";

            var lessMinimumBox = checkboxByText("Can order less than minimum");
            if (applyOrderLessThanMinimum) {
                setCheckbox(lessMinimumBox, canOrderLessThanMinimum);
            }

            var priceIncludesInput = Array.from(document.querySelectorAll(
                '#product-pricing-view input[type="text"]'
            )).find(function (el) {
                return isVisible(el) && /priceIncludes/.test(el.getAttribute("data-bind") || "");
            });
            if (priceIncludesInput) eventValue(priceIncludesInput, priceIncludes);

            var qInputs = Array.from(document.querySelectorAll(
                '#product-pricing-view input[data-select="quantity"]'
            )).filter(isVisible);
            var nInputs = Array.from(document.querySelectorAll(
                '#product-pricing-view input[data-select="net-cost"]'
            )).filter(isVisible);
            var domFilled = Math.min(quantities.length, netCosts.length, qInputs.length, nInputs.length);
            for (var j = 0; j < domFilled; j++) {
                eventValue(qInputs[j], j < domFilled ? quantities[j] : "");
            }
            for (var k = 0; k < domFilled; k++) {
                eventValue(nInputs[k], k < domFilled ? netCosts[k] : "");
            }
            if (priceCodeValue) {
                var discountSelects = Array.from(document.querySelectorAll(
                    '#product-pricing-view select[data-select="discount"]'
                )).filter(isVisible);
                for (var d = 0; d < Math.min(domFilled, discountSelects.length); d++) {
                    discountSelects[d].value = priceCodeValue;
                    discountSelects[d].dispatchEvent(new Event("change", { bubbles: true }));
                    discountSelects[d].dispatchEvent(new Event("blur", { bubbles: true }));
                }
                var applyBox = Array.from(document.querySelectorAll(
                    '#product-pricing-view input[type="checkbox"][id^="chkBoxUseSameCodeForAll_"]'
                )).filter(isVisible)[0];
                if (applyBox && !applyBox.checked) {
                    applyBox.click();
                }
            }

            return {
                modelFilled: modelFilled,
                domFilled: domFilled,
                priceType: priceTypeSelect ? priceTypeSelect.value : null,
                canOrderLessThanMinimum: lessMinimumBox ? !!lessMinimumBox.checked : canOrderLessThanMinimum,
                priceIncludes: priceIncludesInput ? priceIncludesInput.value : null,
                priceCode: priceCodeValue,
                quantities: qInputs.slice(0, quantities.length).map(function (el) { return el.value; }),
                netCosts: nInputs.slice(0, netCosts.length).map(function (el) { return el.value; }),
                modelNetCosts: (function () {
                    try {
                        var grid = firstBaseGrid(vm);
                        var currencyGrid = firstCurrencyGrid(grid);
                        var prices = currencyGrid && currencyGrid.prices ? currencyGrid.prices : (grid && grid.prices ? grid.prices : []);
                        return prices.slice(0, netCosts.length).map(function (p) { return p.netCost ? p.netCost() : ""; });
                    } catch (e) {
                        return [];
                    }
                })()
            };
        """, json.dumps(qties), json.dumps(prcs), price_includes, price_code_value,
            can_order_less_than_minimum, apply_order_less_than_minimum)
        logger.info(f"  Price Type: Net Cost, Price Includes: {price_includes or '(空)'}, Price Code: {payload.get('priceCode') or '(空)'}")
        logger.info(f"  Pricing填充校验: {result}")
        setup_result = self.fill_pricing_setup_upcharges(payload)
        if setup_result and setup_result.get("changed"):
            logger.info(f"  Set-up Charge已填写: {setup_result}")

    def fill_pricing_setup_upcharges(self, payload):
        """Fill Pricing > Upcharges setup charges by imprint location."""
        charges = payload.get("setupCharges") or []
        if not charges:
            return None

        setup_charge_code_value = payload.get("setupChargeCodeValue", "")

        def run(js, *args):
            return self.tab.run_js(js, *args)

        def wait_until(js, timeout=12, interval=0.5):
            start = time.time()
            last = None
            while time.time() - start < timeout:
                try:
                    last = self.tab.run_js(js)
                    if last:
                        return last
                except Exception:
                    pass
                time.sleep(interval)
            return last

        def upcharge_count():
            try:
                return int(run("""
                    var root = document.getElementById('product-pricing-view');
                    var vm = root && window.ko ? ko.dataFor(root) : null;
                    var list = vm && vm.upchargeGridCollection ? vm.upchargeGridCollection() : [];
                    return list ? list.length : 0;
                """) or 0)
            except Exception:
                return 0

        before = upcharge_count()
        open_result = run("""
            var root = document.getElementById('product-pricing-view');
            var vm = root && window.ko ? ko.dataFor(root) : null;
            if (!vm) return {ok:false, error:'pricing vm not found'};
            try { if (typeof vm.onTabChange === 'function') vm.onTabChange('Upcharges'); } catch (e) {}
            var tab = document.querySelector('a[href="#upchargesTabContent"]');
            if (tab) tab.click();
            if (typeof vm.showAddUpchargePop === 'function') vm.showAddUpchargePop();
            return {ok:true};
        """)
        if not open_result or not open_result.get("ok"):
            logger.warning(f"  Upcharge弹窗打开失败: {open_result}")
            return {"changed": [], "requested": charges}

        wait_until("""
            var root = document.getElementById('product-pricing-view');
            var vm = root && window.ko ? ko.dataFor(root) : null;
            var helper = vm && vm.upchargeSetupHelper ? vm.upchargeSetupHelper() : null;
            return helper && helper.currentConfigStep && helper.currentConfigStep() === 1;
        """, timeout=8)

        criteria_result = run("""
            var root = document.getElementById('product-pricing-view');
            var vm = root && window.ko ? ko.dataFor(root) : null;
            if (!vm) return {ok:false, error:'pricing vm not found'};
            var items = vm.availUpchargeSetupTypes ? vm.availUpchargeSetupTypes() : [];
            var item = (items || []).find(function(x) {
                return String(x.criteriaCode || '').toUpperCase() === 'IMLO' ||
                    String(x.criteriaDescription || x.typeDisplayName || '').trim().toLowerCase() === 'imprint location';
            });
            if (!item) return {ok:false, error:'Imprint Location criteria not found'};
            if (typeof item.isSelected === 'function') item.isSelected(true);
            else item.isSelected = true;
            var checks = Array.from(document.querySelectorAll('#upchargeSetupModal input[type="checkbox"]'));
            checks.forEach(function(check) {
                var label = check.closest('label') || check.parentElement;
                var text = (label && label.textContent || '').trim().toLowerCase();
                if (text === 'imprint location' && !check.checked) check.click();
            });
            if (typeof vm.upchargeConfigChange === 'function') vm.upchargeConfigChange('N');
            return {ok:true};
        """)
        if not criteria_result or not criteria_result.get("ok"):
            logger.warning(f"  Upcharge选择 Imprint Location 失败: {criteria_result}")
            return {"changed": [], "requested": charges}

        wait_until("""
            var root = document.getElementById('product-pricing-view');
            var vm = root && window.ko ? ko.dataFor(root) : null;
            var helper = vm && vm.upchargeSetupHelper ? vm.upchargeSetupHelper() : null;
            return helper && helper.currentConfigStep && helper.currentConfigStep() === 2;
        """, timeout=10)

        location_result = run("""
            var charges = JSON.parse(arguments[0] || "[]");
            var root = document.getElementById('product-pricing-view');
            var vm = root && window.ko ? ko.dataFor(root) : null;
            if (!vm) return {ok:false, error:'pricing vm not found'};
            function text(v) { return String(v == null ? '' : v).trim(); }
            function norm(v) { return text(v).toLowerCase().replace(/\\s+/g, ' '); }
            var wanted = charges.map(function(charge) { return norm(charge.location || charge.method); }).filter(Boolean);

            function checkboxText(check) {
                var containers = [
                    check.closest('label'),
                    check.closest('.checkbox'),
                    check.closest('li'),
                    check.parentElement,
                    check.parentElement && check.parentElement.parentElement
                ].filter(Boolean);
                for (var i = 0; i < containers.length; i += 1) {
                    if (
                        containers[i].querySelectorAll &&
                        containers[i].querySelectorAll('input[type="checkbox"]').length > 1
                    ) {
                        continue;
                    }
                    var value = norm(containers[i].textContent);
                    if (value) return value;
                }
                var parts = [];
                var node = check.nextSibling;
                while (node) {
                    if (node.nodeType === Node.TEXT_NODE) parts.push(node.textContent);
                    if (node.nodeType === Node.ELEMENT_NODE) parts.push(node.textContent);
                    node = node.nextSibling;
                }
                return norm(parts.join(' '));
            }
            function wantedMatches(labelText) {
                return wanted.some(function(item) {
                    return labelText === item || labelText.indexOf(item) >= 0 || item.indexOf(labelText) >= 0;
                });
            }
            function setChecked(check) {
                if (!check.checked) check.click();
                ['input', 'change'].forEach(function(name) {
                    check.dispatchEvent(new Event(name, {bubbles:true}));
                });
            }

            var individualClicked = false;
            Array.from(document.querySelectorAll('#upchargeSetupModal input[type="checkbox"]')).forEach(function(check) {
                var labelText = checkboxText(check);
                if (labelText.indexOf('create individual grids') >= 0 && !check.checked) {
                    setChecked(check);
                    individualClicked = true;
                }
                if (labelText && wantedMatches(labelText) && !check.checked) {
                    setChecked(check);
                }
            });

            var matched = [];
            var groups = vm.selectedUpchargeTypesGroups ? vm.selectedUpchargeTypesGroups() : [];
            (groups || []).forEach(function(group) {
                var values = [];
                Object.keys(group || {}).forEach(function(key) {
                    try {
                        var value = typeof group[key] === 'function' ? group[key]() : group[key];
                        if (Array.isArray(value)) values = values.concat(value);
                    } catch (e) {}
                });
                values.forEach(function(item) {
                    var display = norm(item.displayName || item.description || item.value || item.name || item.setCodeValue);
                    if (wanted.indexOf(display) >= 0) {
                        if (typeof item.isSelected === 'function') item.isSelected(true);
                        else item.isSelected = true;
                        if (matched.indexOf(display) < 0) matched.push(display);
                    }
                });
            });

            var buttons = Array.from(document.querySelectorAll('#upchargeSetupModal button'))
                .filter(function(b) { return !!(b.offsetParent || b.getClientRects().length); });
            var button = buttons.find(function(b) {
                return norm(b.textContent) === 'apply' && !b.disabled;
            });
            if (button) {
                button.click();
                return {ok:true, matched:matched, individualClicked:individualClicked, button:'apply'};
            }
            if (typeof vm.upchargeConfigChange === 'function') {
                vm.upchargeConfigChange('AG');
                return {ok:true, matched:matched, individualClicked:individualClicked, button:'vm.upchargeConfigChange'};
            }
            return {ok:false, error:'Apply button not found', matched:matched};
        """, json.dumps(charges, ensure_ascii=False))
        if not location_result or not location_result.get("ok"):
            logger.warning(f"  Upcharge选择 Imprint Location 值失败: {location_result}")
            return {"changed": [], "requested": charges}

        wait_until(f"""
            var root = document.getElementById('product-pricing-view');
            var vm = root && window.ko ? ko.dataFor(root) : null;
            var list = vm && vm.upchargeGridCollection ? vm.upchargeGridCollection() : [];
            return list && list.length >= {before + len(charges)};
        """, timeout=15)

        changed = run("""
                var charges = JSON.parse(arguments[0] || "[]");
                var setupChargeCodeValue = arguments[1] || "";
                var beforeCount = parseInt(arguments[2] || "0", 10);
                var root = document.getElementById('product-pricing-view');
                var vm = root && window.ko ? ko.dataFor(root) : null;
                if (!vm) return [];
                function isObs(fn) { return typeof fn === 'function' && typeof fn.subscribe === 'function'; }
                function unwrap(value) {
                    try {
                        if (value && typeof value.peek === 'function') return value.peek();
                        return typeof value === 'function' ? value() : value;
                    } catch (e) { return null; }
                }
                function norm(v) {
                    return String(v == null ? '' : v).trim().toLowerCase().replace(/\\s+/g, ' ');
                }
                function setObs(target, name, value) {
                    if (!target || value === undefined || value === null) return false;
                    if (isObs(target[name])) target[name](value);
                    else target[name] = value;
                    return true;
                }
                function setDiscount(price, code, applyToAll) {
                    if (!price || !code) return false;
                    var discountRate = unwrap(price.discountRate) || price.discountRate;
                    if (discountRate) {
                        if (isObs(discountRate.industryDiscountCode)) discountRate.industryDiscountCode(code);
                        else discountRate.industryDiscountCode = code;
                    }
                    ['discountCode', 'discountRateCode', 'industryDiscountCode'].forEach(function(name) {
                        setObs(price, name, code);
                    });
                    if (isObs(price.isApplyOneCodeEnabled)) price.isApplyOneCodeEnabled(!!applyToAll);
                    return true;
                }
                function chargeName(charge) {
                    return charge.location || charge.method || '';
                }
                function chargeCode(charge) {
                    return charge.code || setupChargeCodeValue || '';
                }
                function fillPriceArray(prices, charge) {
                    if (!prices || !prices.length) return 0;
                    var code = chargeCode(charge);
                    prices.forEach(function(price, index) {
                        var active = index === 0;
                        setObs(price, 'isEnabled', active);
                        if (active) {
                            setObs(price, 'quantity', '1');
                            setObs(price, 'netCost', charge.amount);
                            setObs(price, 'listPrice', '');
                            if (isObs(price.isQUR)) price.isQUR(false);
                            setDiscount(price, code, true);
                        } else {
                            setObs(price, 'quantity', '');
                            setObs(price, 'netCost', '');
                            setObs(price, 'listPrice', '');
                        }
                    });
                    return 1;
                }
                var grids = vm.upchargeGridCollection ? vm.upchargeGridCollection() : [];
                var created = (grids || []).slice(beforeCount);
                var used = [];
                var changed = [];

                charges.forEach(function(charge, index) {
                    var name = chargeName(charge);
                    var code = chargeCode(charge);
                    var wanted = norm(name);
                    var gridIndex = -1;
                    for (var i = 0; i < created.length; i++) {
                        if (used.indexOf(i) >= 0) continue;
                        var desc = norm(unwrap(created[i].description)) + ' ' + norm(unwrap(created[i].configDesc));
                        if (desc.indexOf(wanted) >= 0) {
                            gridIndex = i;
                            break;
                        }
                    }
                    if (gridIndex < 0) gridIndex = used.length < created.length ? used.length : -1;
                    if (gridIndex < 0 || !created[gridIndex]) return;
                    used.push(gridIndex);

                    var grid = created[gridIndex];
                    setObs(grid, 'description', name);
                    setObs(grid, 'priceIncludes', '');
                    setObs(grid, 'priceGridSubTypeCode', 'STCH');
                    setObs(grid, 'usageLevelCode', 'NONE');
                    setObs(grid, 'requiredFlag', 'Y');
                    setObs(grid, 'isQUR', false);

                    var filled = 0;
                    var gridPrices = grid.prices ? (typeof grid.prices === 'function' ? grid.prices() : grid.prices) : [];
                    filled = Math.max(filled, fillPriceArray(gridPrices, charge));
                    var currencyGrids = grid.currencyGrids ? (typeof grid.currencyGrids === 'function' ? grid.currencyGrids() : grid.currencyGrids) : [];
                    (currencyGrids || []).forEach(function(currencyGrid) {
                        var prices = currencyGrid.prices ? (typeof currencyGrid.prices === 'function' ? currencyGrid.prices() : currencyGrid.prices) : [];
                        filled = Math.max(filled, fillPriceArray(prices, charge));
                    });

                    var id = unwrap(grid.id);
                    var body = id ? document.getElementById('ugid_' + id) : null;
                    var chevron = id ? document.getElementById('chevronu_' + id) : null;
                    if (chevron && body && !body.classList.contains('in')) chevron.click();
                    if (body) {
                        body.classList.add('in');
                        body.style.height = 'auto';
                    }
                    var pane = body ? body.closest('.accordion-group') : null;
                    if (pane) {
                        var title = pane.querySelector('.accordion-heading input[type="text"]');
                        if (title) {
                            title.value = name;
                            title.dispatchEvent(new Event('input', {bubbles:true}));
                            title.dispatchEvent(new Event('change', {bubbles:true}));
                        }
                        var typeSelect = Array.from(pane.querySelectorAll('select')).find(function(select) {
                            return Array.from(select.options || []).some(function(option) { return option.value === 'STCH'; });
                        });
                        if (typeSelect) {
                            typeSelect.value = 'STCH';
                            typeSelect.dispatchEvent(new Event('change', {bubbles:true}));
                        }
                        var usageSelects = Array.from(pane.querySelectorAll('select.upch-usage'));
                        if (usageSelects[0]) {
                            usageSelects[0].value = 'NONE';
                            usageSelects[0].dispatchEvent(new Event('change', {bubbles:true}));
                        }
                        if (usageSelects[1]) {
                            usageSelects[1].value = 'Y';
                            usageSelects[1].dispatchEvent(new Event('change', {bubbles:true}));
                        }
                        var qInputs = Array.from(pane.querySelectorAll('input[data-select="quantity"]'));
                        var nInputs = Array.from(pane.querySelectorAll('input[data-select="net-cost"]'));
                        if (qInputs[0]) {
                            qInputs[0].value = '1';
                            qInputs[0].dispatchEvent(new Event('input', {bubbles:true}));
                            qInputs[0].dispatchEvent(new Event('change', {bubbles:true}));
                            qInputs[0].dispatchEvent(new Event('blur', {bubbles:true}));
                        }
                        if (nInputs[0]) {
                            nInputs[0].value = charge.amount;
                            nInputs[0].dispatchEvent(new Event('input', {bubbles:true}));
                            nInputs[0].dispatchEvent(new Event('change', {bubbles:true}));
                            nInputs[0].dispatchEvent(new Event('blur', {bubbles:true}));
                        }
                        var discount = Array.from(pane.querySelectorAll('select[data-select="discount"]'))[0];
                        if (discount && code) {
                            discount.value = code;
                            discount.dispatchEvent(new Event('change', {bubbles:true}));
                        }
                        var applyBox = pane.querySelector('input[type="checkbox"][id^="chkBoxUseSameCodeForAll_"]');
                        if (applyBox && !applyBox.checked) applyBox.click();
                    }
                    changed.push({
                        ok: true,
                        location: name,
                        amount: charge.amount,
                        setupChargeCode: code,
                        gridId: id,
                        filled: filled
                    });
                });

                if (isObs(vm.isDirty)) vm.isDirty(true);
                return changed;
            """, json.dumps(charges, ensure_ascii=False), setup_charge_code_value, str(before))

        return {"changed": changed, "requested": charges}

    def fill_availability(self, row):
        """填充 Availability Tab"""
        logger.info("填充 Availability...")
        click_tab(self.tab, "#/product/availability")
        time.sleep(3)
        logger.info("  当前 Excel 没有 Availability 专用字段，跳过")

    def fill_media(self, row, image_root_path):
        """Upload Media tab images with enhanced robustness.

        Key fixes:
        - Waits for Knockout.js view model to fully initialize before file selection
        - Uses JS to set files and forcefully trigger the native change event
        - Retries once on failure with a fresh file input lookup
        - Returns blocking=True when upload fails (critical: prevents Make Active without images)
        """
        logger.info("Media tab - uploading images...")
        click_tab(self.tab, "#/product/media")
        time.sleep(3)

        product_no = safe_str(row.get("Product_Number", ""))
        folder = get_product_image_folder(image_root_path, product_no)
        image_files = list(iter_image_files(folder)) if folder else []
        if not image_files:
            logger.warning("  No image files found for upload")
            return {"blocking": False, "message": "No image files found"}

        product_colors = self.parse_product_colors(
            self.row_value(row, "Product_Color", "Product Color")
        )

        logger.info(
            f"  Images to upload: {', '.join(Path(p).name for p in image_files)}"
        )

        # Attempt upload with one retry
        for attempt in range(2):
            if attempt > 0:
                logger.info(f"  Retry image upload (attempt {attempt + 1})...")
                time.sleep(5)
                # Re-navigate to ensure tab state is fresh
                click_tab(self.tab, "#/product/media")
                time.sleep(4)
                self._dismiss_all_alerts()

            result = self._upload_images_once(image_files, product_colors)
            if result.get("success"):
                return result
            if result.get("blocking") and attempt < 1:
                # Only retry on blocking failures (upload did not work at all)
                continue
            if result.get("blocking"):
                return result
            # Partial success is acceptable; return it
            return result

        return {"blocking": True, "message": "Image upload failed after retry"}

    def _upload_images_once(self, image_files, product_colors):
        """Single image upload attempt - simplified and robust."""
        try:
            self._dismiss_all_alerts()

            ready = self._wait_for_media_tab_ready(timeout=20)
            if not ready:
                return {"blocking": True, "message": "Media tab KO view model not ready"}

            before_visible = self.get_visible_media_count()

            file_input = self.tab.ele("@id=image-to-upload", timeout=10)
            if not file_input:
                return {"blocking": True, "message": "File input element not found"}

            file_paths_str = chr(10).join(str(Path(f).resolve()) for f in image_files)
            file_input.input(file_paths_str)
            time.sleep(1)

            # DrissionPage sets files through CDP DOM.setFileInputFiles. Chrome
            # dispatches input/change for that operation, so dispatching change
            # again would enqueue the same ASI upload batch a second time.
            logger.info(f"  Files selected: {len(image_files)} file(s)")

            uploaded = self._wait_for_image_upload(
                before_visible_count=before_visible,
                expected_count=len(image_files),
                timeout=120,
            )

            if uploaded <= 0:
                debug = self.get_media_upload_debug_state()
                logger.warning(f"  Upload failed - debug state: {debug}")
                return {"blocking": True, "message": f"Image upload failed: 0/{len(image_files)} uploaded"}

            logger.info(f"  Upload complete: {uploaded}/{len(image_files)}")

            self._dismiss_all_alerts()

            tag_result = self.apply_media_criteria_tags(image_files, product_colors)
            logger.info(f"  Criteria tags: {tag_result}")

            media_save = self.save_current_tab("Media", missing_ok=True)
            if media_save and media_save.get("blocking"):
                return media_save

            return {"blocking": False, "success": True, "message": f"Image upload: {uploaded}/{len(image_files)}"}

        except Exception as e:
            logger.error(f"  Image upload error: {e}")
            return {"blocking": True, "message": f"Image upload error: {e}"}


    def _wait_for_media_tab_ready(self, timeout=20):
        """Wait until the Media tab Knockout view model is initialized."""
        start = time.time()
        while time.time() - start < timeout:
            ready = self._run_js_safe("""
                var input = document.getElementById("image-to-upload");
                var view = document.getElementById("product-images-view");
                if (!input || !view) return false;
                var vm = window.ko && ko.dataFor(view);
                if (!vm) return false;
                // Check that the upload handler is bound
                var bindAttr = input.getAttribute("data-bind") || "";
                if (bindAttr.indexOf("upload") < 0) return false;
                return true;
            """)
            if ready:
                logger.info("  Media tab ready (KO view model initialized)")
                return True
            time.sleep(1)
        logger.warning("  Media tab not ready within timeout")
        return False

    def _wait_for_image_upload(self, before_visible_count, expected_count, timeout=120):
        """等待页面上传完成：监控 spinner 和图片数量变化。
        
        与 wait_media_count_at_least 不同，此方法不依赖 API 计数，
        仅通过 DOM 可见的图片数量和 spinner 状态判断。
        """
        start = time.time()
        spinner_disappeared = False
        last_log_at = 0

        while time.time() - start < timeout:
            elapsed = time.time() - start

            # 检查弹窗
            alert_text = self.handle_any_alert()
            if alert_text:
                logger.warning(f"  上传过程中弹窗: {alert_text[:80]}")

            # 检查 spinner 状态
            try:
                spinner_visible = self._run_js_safe("""return (function() {
                    var sp = document.getElementById('image-upload-spinner');
                    return !!(sp && sp.offsetParent);
                })()""")
            except Exception:
                spinner_visible = False

            if not spinner_visible and elapsed > 3:
                spinner_disappeared = True

            # 检查当前可见图片数量
            current_count = self.get_visible_media_count()

            # spinner 已经消失，且图片数量增加了
            if spinner_disappeared and current_count is not None and before_visible_count is not None:
                if current_count >= before_visible_count + expected_count:
                    logger.info(f"  图片上传检测: {current_count} 张 (新增 {current_count - before_visible_count})")
                    return current_count - before_visible_count

            # spinner 已经消失但数量没增加，再等一会确认
            if spinner_disappeared and current_count is not None and before_visible_count is not None:
                # 如果之前有图片，但数量只增加了部分，继续等待
                if current_count > before_visible_count:
                    # 额外等待确认不再变化
                    time.sleep(3)
                    second_check = self.get_visible_media_count()
                    if second_check == current_count and second_check > before_visible_count:
                        logger.info(f"  图片上传部分完成: {second_check - before_visible_count}/{expected_count}")
                        return second_check - before_visible_count

            # spinner 消失超过10秒且没有新增图片，可能上传失败
            if spinner_disappeared and elapsed > 15 and current_count is not None and before_visible_count is not None:
                if current_count <= before_visible_count:
                    logger.warning(f"  spinner 已消失但无新增图片，等待确认...")
                    if elapsed > 25:
                        logger.warning(f"  等待超时，无新增图片")
                        return 0

            if elapsed - last_log_at >= 10:
                logger.info(
                    f"  图片上传等待: {int(elapsed)}s, "
                    f"visible_count={current_count}, spinner={spinner_visible}"
                )
                last_log_at = elapsed

            time.sleep(2)

        logger.warning(f"  图片上传等待超时 ({timeout}s)")
        # 超时后最后检查一次
        final_count = self.get_visible_media_count()
        if final_count is not None and before_visible_count is not None and final_count > before_visible_count:
            return final_count - before_visible_count
        return 0

    def apply_media_criteria_tags(self, image_files, available_colors=None):
        available_colors = available_colors or []
        image_tags = self.media_tags_for_files(image_files, available_colors)
        if not image_tags:
            return {"changed": [], "message": "no color tags"}
        try:
            return self.tab.run_js("""
                var imageTags = JSON.parse(arguments[0] || "[]");
                var fallbackColors = JSON.parse(arguments[1] || "[]");
                var root = document.getElementById('product-images-view');
                var vm = root && window.ko ? ko.dataFor(root) : null;
                var modal = document.getElementById('attachCriteriaTags');
                function unwrap(value) {
                    try {
                        if (value && typeof value.peek === 'function') return value.peek();
                        return typeof value === 'function' ? value() : value;
                    } catch (e) {
                        return null;
                    }
                }
                function setObs(target, key, value) {
                    if (!target) return false;
                    if (typeof target[key] === 'function') target[key](value);
                    else target[key] = value;
                    return true;
                }
                function stem(name) {
                    return String(name || '').replace(/\\.[^.]*$/, '').trim();
                }
                function norm(value) {
                    return String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                }
                function normalizeColorText(value) {
                    return String(value || '')
                        .replace(/[()_+\\-,;]+/g, ' ')
                        .replace(/\\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                }
                function firstText() {
                    for (var i = 0; i < arguments.length; i++) {
                        var value = unwrap(arguments[i]);
                        if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
                    }
                    return '';
                }
                function colorNameFromCandidate(candidate) {
                    var text = firstText(
                        candidate.criteriaValue,
                        candidate.criteriaValueDisplayName,
                        candidate.value,
                        candidate.name,
                        candidate.displayName,
                        candidate.fullDisplayName,
                        candidate.criteriaDetail,
                        candidate.setCodeValue
                    );
                    if (!text) return '';
                    if (text.indexOf(':') >= 0) text = text.split(':').pop();
                    return text.trim();
                }
                function addProductColor(colors, label) {
                    label = String(label || '').replace(/\\s+/g, ' ').trim();
                    if (!label) return;
                    var aliases = [label];
                    var match = label.match(/^(.+?)\\s*\\((.+?)\\)$/);
                    if (match) aliases.push(match[1].trim(), match[2].trim());
                    colors[label] = {label: label, aliases: aliases};
                }
                function isProductColorCandidate(candidate) {
                    var descriptor = [
                        firstText(candidate.criteriaSetName),
                        firstText(candidate.criteriaName),
                        firstText(candidate.criteriaDescription),
                        firstText(candidate.typeDisplayName),
                        firstText(candidate.fullDisplayName),
                        firstText(candidate.displayName)
                    ].join(' ');
                    return norm(descriptor).indexOf('product color') >= 0 ||
                        String(firstText(candidate.criteriaSetCode, candidate.criteriaCode, candidate.setCode)).toUpperCase() === 'PRCL';
                }
                function collectProductColorsFromObject(value, out, depth) {
                    if (!value || depth > 5) return;
                    var unwrapped = unwrap(value);
                    if (!unwrapped) return;
                    if (Array.isArray(unwrapped)) {
                        unwrapped.forEach(function(item) { collectProductColorsFromObject(item, out, depth + 1); });
                        return;
                    }
                    if (typeof unwrapped !== 'object') return;
                    if (isProductColorCandidate(unwrapped)) {
                        var color = colorNameFromCandidate(unwrapped);
                        if (color) addProductColor(out, color);
                    }
                    Object.keys(unwrapped).forEach(function(key) {
                        if (/parent|root|productMedia|scrolledImageList/i.test(key)) return;
                        var child = null;
                        try { child = unwrap(unwrapped[key]); } catch (e) { child = null; }
                        if (Array.isArray(child)) collectProductColorsFromObject(child, out, depth + 1);
                    });
                }
                function collectAvailableProductColors() {
                    var colors = {};
                    if (vm) {
                        [
                            'selectedColors',
                            'productCriteriaSets',
                            'selectedCriteriaTypesGroups',
                            'criteriaSets',
                            'availableCriteriaSets'
                        ].forEach(function(key) {
                            if (vm[key]) collectProductColorsFromObject(vm[key], colors, 0);
                        });
                    }
                    Array.from(document.querySelectorAll('#attachCriteriaTags label, #attachCriteriaTags .controls-row, #attachCriteriaTags span, #attachCriteriaTags div')).forEach(function(el) {
                        var text = (el.textContent || '').replace(/\\s+/g, ' ').trim();
                        var match = text.match(/^(.+?)\\s*\\((.+?)\\)$/);
                        if (match) addProductColor(colors, match[0]);
                    });
                    (fallbackColors || []).forEach(function(color) {
                        addProductColor(colors, color);
                    });
                    return Object.keys(colors).map(function(key) { return colors[key]; }).sort(function(a, b) {
                        var aLongest = Math.max.apply(null, a.aliases.map(function(x) { return x.length; }));
                        var bLongest = Math.max.apply(null, b.aliases.map(function(x) { return x.length; }));
                        return bLongest - aLongest;
                    });
                }
                function colorsFromStem(stem, availableColors) {
                    var text = normalizeColorText(stem);
                    text = text.replace(/^\\d+(?:\\s+|$)/, ' ');
                    var matched = [];
                    availableColors.forEach(function(colorSpec) {
                        var aliases = (colorSpec.aliases || [colorSpec.label]).slice().sort(function(a, b) { return b.length - a.length; });
                        for (var i = 0; i < aliases.length; i++) {
                            var colorText = normalizeColorText(aliases[i]);
                            if (!colorText) continue;
                            var re = new RegExp('(^|\\\\s)' + colorText.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&') + '(?=\\\\s|$)');
                            if (re.test(text)) {
                                matched.push(colorSpec.label);
                                text = text.replace(re, ' ');
                                break;
                            }
                        }
                    });
                    return matched;
                }
                function delay(ms) {
                    return new Promise(function(resolve) { setTimeout(resolve, ms); });
                }
                function isVisible(el) {
                    return !!(el && (el.offsetParent || el.getClientRects().length));
                }
                function clickElement(el) {
                    if (!el) return false;
                    el.scrollIntoView({block:'center', inline:'center'});
                    el.click();
                    return true;
                }
                function findButton(container, buttonText) {
                    var wantedText = norm(buttonText);
                    return Array.from((container || document).querySelectorAll('button, a')).find(function(el) {
                        return norm(el.textContent).indexOf(wantedText) >= 0;
                    }) || null;
                }
                function checkboxText(check) {
                    var containers = [
                        check.closest('label'),
                        check.closest('.checkbox'),
                        check.parentElement,
                        check.parentElement && check.parentElement.parentElement
                    ].filter(Boolean);
                    for (var i = 0; i < containers.length; i += 1) {
                        if (
                            containers[i].querySelectorAll &&
                            containers[i].querySelectorAll('input[type="checkbox"]').length > 1
                        ) {
                            continue;
                        }
                        var value = text(containers[i].textContent);
                        if (value) return value;
                    }
                    var parts = [];
                    var node = check.nextSibling;
                    while (node) {
                        if (node.nodeType === Node.TEXT_NODE) parts.push(node.textContent);
                        if (node.nodeType === Node.ELEMENT_NODE) parts.push(node.textContent);
                        node = node.nextSibling;
                    }
                    return text(parts.join(' '));
                }
                function colorParts(value) {
                    var raw = text(value);
                    var match = raw.match(/^(.+?)\\s*\\((.+?)\\)$/);
                    if (!match) {
                        return {
                            whole: normalizeColorText(raw),
                            primary: normalizeColorText(raw),
                            paren: '',
                            hasParen: false
                        };
                    }
                    return {
                        whole: normalizeColorText(raw),
                        primary: normalizeColorText(match[1]),
                        paren: normalizeColorText(match[2]),
                        hasParen: true
                    };
                }
                function colorLabelMatches(label, target) {
                    var labelParts = colorParts(label);
                    var targetParts = colorParts(target);
                    var candidates = [targetParts.whole, targetParts.primary];
                    if (targetParts.hasParen) candidates.push(targetParts.paren);
                    return candidates.some(function(candidate) {
                        return !!candidate && (
                            labelParts.whole === candidate ||
                            labelParts.primary === candidate ||
                            (targetParts.hasParen && labelParts.paren === candidate)
                        );
                    });
                }
                function setChecked(check) {
                    if (!check.checked) check.click();
                    ['input', 'change'].forEach(function(name) {
                        check.dispatchEvent(new Event(name, {bubbles:true}));
                    });
                }
                async function waitForModalReady(timeoutMs) {
                    var start = Date.now();
                    while (Date.now() - start < timeoutMs) {
                        modal = document.getElementById('attachCriteriaTags');
                        if (isVisible(modal) && modal.querySelectorAll('input[type="checkbox"]').length) {
                            return true;
                        }
                        await delay(200);
                    }
                    return false;
                }
                async function waitForModalClosed(timeoutMs) {
                    var start = Date.now();
                    while (Date.now() - start < timeoutMs) {
                        modal = document.getElementById('attachCriteriaTags');
                        if (!isVisible(modal)) return true;
                        await delay(200);
                    }
                    return false;
                }
                function selectedTokenTexts(li) {
                    if (!li) return [];
                    return Array.from(li.querySelectorAll('.selected-criteria-sets li, .token-input-token-facebook'))
                        .map(function(el) { return text(el.textContent); })
                        .filter(Boolean);
                }
                function collectMediaRows() {
                    if (!root) return [];
                    var rows = Array.from(root.querySelectorAll('li.image-list-item, .image-list-item'));
                    if (rows.length) return rows;
                    var buttons = Array.from(root.querySelectorAll('button, a')).filter(function(el) {
                        return norm(el.textContent).indexOf('add tags') >= 0;
                    });
                    return buttons.map(function(button) {
                        return button.closest('li') || button.closest('.row-fluid') || button.parentElement;
                    }).filter(Boolean);
                }
                var items = vm && vm.scrolledImageList ? unwrap(vm.scrolledImageList) || [] : [];
                var lis = collectMediaRows();
                var availableColors = collectAvailableProductColors();
                var byStem = {};
                items.forEach(function(item, index) {
                    var media = unwrap(item.media) || {};
                    var fileName = stem(unwrap(media.fileName));
                    if (fileName) byStem[norm(fileName)] = {item:item, index:index};
                });
                lis.forEach(function(li, index) {
                    var rowText = norm(li.textContent);
                    imageTags.forEach(function(spec) {
                        if (spec.stem && rowText.indexOf(norm(spec.stem)) >= 0 && !byStem[norm(spec.stem)]) {
                            byStem[norm(spec.stem)] = {item:items[index], index:index};
                        }
                    });
                });
                async function run() {
                    var changed = [];
                    for (var index = 0; index < imageTags.length; index += 1) {
                        var spec = imageTags[index];
                        var colors = Array.isArray(spec.colors) && spec.colors.length
                            ? spec.colors
                            : colorsFromStem(spec.stem, availableColors);
                        if (!colors.length) {
                            changed.push({stem: spec.stem, skipped:true, reason:'no colors matched'});
                            continue;
                        }
                        var found = byStem[norm(spec.stem)] || null;
                        var item = found ? found.item : items[index];
                        var li = found ? lis[found.index] : lis[index];
                        var opened = false;
                        if (vm && typeof vm.openImageCriteriaTagsModal === 'function' && item) {
                            try {
                                vm.openImageCriteriaTagsModal.call(item, found ? found.index : index);
                                await delay(300);
                                opened = isVisible(modal);
                            } catch (e) {}
                        }
                        if (!opened) {
                            opened = clickElement(findButton(li, 'Add Tags'));
                        }
                        if (!opened) {
                            changed.push({stem: spec.stem, colors: colors, opened:false, error:'Add Tags button not found'});
                            continue;
                        }
                        var ready = await waitForModalReady(8000);
                        if (!ready) {
                            changed.push({stem: spec.stem, colors: colors, opened:true, error:'criteria modal not ready'});
                            continue;
                        }
                        var selected = [];
                        Array.from(modal.querySelectorAll('input[type="checkbox"]')).forEach(function(check) {
                            var labelText = checkboxText(check);
                            if (colors.some(function(color) { return colorLabelMatches(labelText, color); })) {
                                setChecked(check);
                                selected.push(labelText);
                            }
                        });
                        var save = modal.querySelector('.attachCriteriaTags') || findButton(modal, 'Save');
                        var saved = clickElement(save);
                        var closed = saved ? await waitForModalClosed(8000) : false;
                        changed.push({
                            stem: spec.stem,
                            colors: colors,
                            opened: opened,
                            selected: selected,
                            saved: saved,
                            closed: closed,
                            tokensAfter: selectedTokenTexts(li)
                        });
                    }
                    if (vm && typeof vm.isDirty === 'function') vm.isDirty(true);
                    return {ok:true, availableColors:availableColors, changed:changed};
                }
                return run();
            """, json.dumps(image_tags, ensure_ascii=False), json.dumps(available_colors, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"  图片Criteria Tags失败: {e}")
            return {"ok": False, "error": safe_str(e), "tags": image_tags}

    def get_media_upload_debug_state(self):
        """Collect visible Media tab status after an upload attempt."""
        try:
            return self.tab.run_js("""
                var root = document.getElementById('product-images-view') || document.body;
                function visible(el) { return !!(el && (el.offsetParent || el.getClientRects().length)); }
                var input = document.getElementById('image-to-upload');
                var spinner = document.getElementById('image-upload-spinner');
                var interesting = Array.from(root.querySelectorAll('.alert, .label-warning, .label-important, .text-error, .validationMessage, [class*="error"], [class*="Error"]'))
                    .filter(visible)
                    .map(function(el) { return (el.textContent || '').trim(); })
                    .filter(Boolean)
                    .slice(0, 20);
                var manageText = Array.from(root.querySelectorAll('*'))
                    .map(function(el) { return (el.textContent || '').trim(); })
                    .find(function(text) { return /^Manage Images \\(\\d+\\)$/.test(text); });
                return {
                    fileCount: input && input.files ? input.files.length : null,
                    fileNames: input && input.files ? Array.from(input.files).map(function(f) { return f.name; }) : [],
                    imagePath: document.getElementById('filename') ? document.getElementById('filename').value : null,
                    spinnerVisible: !!(spinner && spinner.offsetParent),
                    manageText: manageText || null,
                    messages: interesting
                };
            """)
        except Exception as e:
            return {"error": safe_str(e)}

    def get_visible_media_count(self):
        """Read image count from the Media tab DOM."""
        try:
            count = self.tab.run_js("""
                var manageText = Array.from(document.querySelectorAll('#product-images-view *'))
                    .map(function(el) { return (el.textContent || '').trim(); })
                    .find(function(text) { return /^Manage Images \\(\\d+\\)$/.test(text); });
                if (manageText) {
                    var m = manageText.match(/\\((\\d+)\\)/);
                    if (m) return parseInt(m[1], 10);
                }
                var names = Array.from(document.querySelectorAll('#product-images-view a[href*="media.asicdn.com"], #product-images-view img'))
                    .filter(function(el) { return !!(el.offsetParent || el.getClientRects().length); });
                return names.length || 0;
            """)
            return int(count or 0)
        except Exception:
            return None

    def wait_media_count_at_least(self, product_id, before_count, expected_added, timeout=90, before_visible_count=None):
        """Wait until ProductMedia item count increases by expected count."""
        start = time.time()
        last_count = before_count
        last_visible_count = before_visible_count
        spinner_seen = False
        last_log_at = 0
        while time.time() - start < timeout:
            time.sleep(2)
            elapsed = time.time() - start
            alert_text = self.handle_any_alert()
            if alert_text:
                return 0
            count = self.get_product_media_count(product_id)
            if count is not None:
                last_count = count
            if before_count is not None and count is not None and count >= before_count + expected_added:
                return count - before_count

            visible_count = self.get_visible_media_count()
            if visible_count is not None:
                last_visible_count = visible_count
            if (
                before_visible_count is not None
                and visible_count is not None
                and visible_count >= before_visible_count + expected_added
            ):
                return visible_count - before_visible_count

            spinner_visible = self.tab.run_js("""
                var sp = document.getElementById('image-upload-spinner');
                return !!(sp && sp.offsetParent);
            """)
            if spinner_visible:
                spinner_seen = True
            if elapsed - last_log_at >= 10:
                logger.info(
                    f"  图片上传等待: {int(elapsed)}s, api_count={last_count}, "
                    f"visible_count={last_visible_count}, spinner={spinner_visible}"
                )
                last_log_at = elapsed
            if elapsed < 20:
                continue
            if not spinner_visible and spinner_seen and before_count is not None and last_count is not None:
                return max(0, last_count - before_count)
            if not spinner_visible and spinner_seen and before_visible_count is not None and last_visible_count is not None:
                return max(0, last_visible_count - before_visible_count)
            if not spinner_visible and spinner_seen and before_count is None:
                return expected_added
        if before_count is not None and last_count is not None:
            return max(0, last_count - before_count)
        if before_visible_count is not None and last_visible_count is not None:
            return max(0, last_visible_count - before_visible_count)
        return 0

    # =========================
    # 保存
    # =========================

    def save_current_tab(self, tab_name, missing_ok=False):
        """保存当前Tab并处理结果"""
        logger.info(f"  保存 {tab_name}...")
        if click_save_button(self.tab, log_missing=not missing_ok):
            result = wait_and_handle_alert(self.tab, timeout=30)
            if result is None:
                logger.info(f"  {tab_name} 保存成功")
                return None
            elif result.get("blocking"):
                logger.error(f"  {tab_name} 失败: {result['message'][:80]}")
            else:
                logger.warning(f"  {tab_name} 警告: {result['message'][:80]}")
            return result
        else:
            if missing_ok:
                logger.info(f"  {tab_name} 未找到 Save 按钮，视为不需要保存")
                return None
            logger.warning(f"  {tab_name} 未找到Save按钮")
            return {"blocking": False, "message": "未找到Save按钮"}

    # =========================
    # Make Active
    # =========================

    def _click_make_active(self):
        """Trigger the ASI Make Active command by clicking the visible button (same as manual click)."""
        raw = self.tab.run_js(r"""
            function visible(el) {
                return !!(el && (el.offsetParent || el.getClientRects().length));
            }
            // Priority 1: click the visible Make Active button (same as real user click)
            var buttons = Array.from(document.querySelectorAll('button, a'));
            for (var j = 0; j < buttons.length; j++) {
                var b = buttons[j];
                var text = (b.textContent || '').replace(/\s+/g, ' ').trim();
                var bind = b.getAttribute('data-bind') || '';
                var disabled = !!(b.disabled || b.classList.contains('disabled') || b.getAttribute('aria-disabled') === 'true');
                if ((/^Make Active$/i.test(text) || bind.indexOf('publishProduct') !== -1) && visible(b)) {
                    if (disabled) {
                        // Try enabling first, then click
                        b.removeAttribute('disabled');
                        b.classList.remove('disabled');
                        b.click();
                        return JSON.stringify({ clicked: true, method: 'button_forced', text: text, wasDisabled: true });
                    }
                    b.click();
                    return JSON.stringify({ clicked: true, method: 'button', text: text });
                }
            }
            // Priority 2: fallback to KO viewmodel command
            function runCommand(cmd) {
                if (!cmd) return false;
                if (typeof cmd.execute === 'function') {
                    cmd.execute();
                    return true;
                }
                if (typeof cmd === 'function') {
                    cmd();
                    return true;
                }
                return false;
            }
            try {
                if (window.ko) {
                    var nodes = Array.from(document.querySelectorAll('[data-bind*="publishProduct"], button'));
                    for (var i = 0; i < nodes.length; i++) {
                        var vm = ko.dataFor(nodes[i]);
                        if (vm && runCommand(vm.publishProduct)) {
                            return JSON.stringify({ clicked: true, method: 'ko.publishProduct' });
                        }
                    }
                }
            } catch (e) {}
            return JSON.stringify({ clicked: false, reason: 'not_found' });
        """)
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {"clicked": bool(raw), "raw": safe_str(raw)}

    def _confirm_make_active_if_needed(self):
        """Confirm the price/product confirmation modal when ASI asks for it."""
        raw = self.tab.run_js(r"""
            function visible(el) {
                return !!(el && (el.offsetParent || el.getClientRects().length));
            }
            var modals = Array.from(document.querySelectorAll('[class*="confirmProductModal"], [id*="confirmProductModal"]'))
                .filter(visible);
            if (!modals.length) {
                return JSON.stringify({ handled: false });
            }
            var modal = modals[0];
            var input = modal.querySelector('input[type="text"]');
            if (input && !input.value) {
                var d = new Date();
                d.setFullYear(d.getFullYear() + 1);
                var mm = String(d.getMonth() + 1).padStart(2, '0');
                var dd = String(d.getDate()).padStart(2, '0');
                input.value = mm + '/' + dd + '/' + d.getFullYear();
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
            }
            var buttons = Array.from(modal.querySelectorAll('button'));
            var confirm = buttons.find(function(btn) {
                return /^Confirm$/i.test((btn.textContent || '').trim());
            });
            if (confirm) {
                confirm.click();
                return JSON.stringify({ handled: true, action: 'confirm' });
            }
            return JSON.stringify({ handled: true, action: 'modal_without_confirm_button' });
        """)
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {"handled": bool(raw), "raw": safe_str(raw)}

    def _submit_restricted_words_for_review_if_needed(self):
        """Submit the product for review when ASI blocks activation on restricted words."""
        raw = self.tab.run_js(r"""
            function visible(el) {
                return !!(el && (el.offsetParent || el.getClientRects().length));
            }
            var modals = Array.from(document.querySelectorAll('[class*="validationModal"], [id*="validationModal"]'))
                .filter(visible);
            if (!modals.length) {
                return JSON.stringify({ handled: false });
            }
            var modal = modals[0];
            var buttons = Array.from(modal.querySelectorAll('button')).filter(visible);
            var submit = buttons.find(function(btn) {
                var text = (btn.textContent || '').replace(/\s+/g, ' ').trim();
                var bind = btn.getAttribute('data-bind') || '';
                return /Submit Product.*Review/i.test(text) || bind.indexOf('SubmitWithRestrictedWord') !== -1;
            });
            if (submit) {
                submit.click();
                return JSON.stringify({ handled: true, action: 'submit_review', text: (submit.textContent || '').trim() });
            }
            return JSON.stringify({ handled: false, validationVisible: true });
        """)
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {"handled": bool(raw), "raw": safe_str(raw)}

    def _read_make_active_modal_state(self):
        raw = self.tab.run_js(r"""
            function visible(el) {
                return !!(el && (el.offsetParent || el.getClientRects().length));
            }
            function visibleText(el) {
                return (el && (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim()) || '';
            }
            function firstVisible(selector) {
                var modals = Array.from(document.querySelectorAll(selector)).filter(visible);
                return modals.length ? modals[0] : null;
            }
            function modalText(selector) {
                return visibleText(firstVisible(selector));
            }
            function validationText(selector) {
                var modal = firstVisible(selector);
                if (!modal) return '';
                // Collect errors from all error tables (validation + restricted)
                var parts = [];
                var errorTables = modal.querySelectorAll('[id*="validationErrors"], [id*="restrictedErrors"]');
                for (var t = 0; t < errorTables.length; t++) {
                    var tableId = errorTables[t].id || '';
                    var prefix = tableId.indexOf('restricted') !== -1 ? '[RestrictedWord] ' : '';
                    var rows = Array.from(errorTables[t].querySelectorAll('tbody tr'))
                        .filter(visible)
                        .map(function(row) { return prefix + visibleText(row); })
                        .filter(Boolean);
                    parts = parts.concat(rows);
                }
                if (parts.length) return parts.join('; ');
                return visibleText(modal);
            }
            var success = modalText('[class*="publishSuccessModal"], [id*="publishSuccessModal"]');
            if (success) return JSON.stringify({ state: 'success', message: success });
            var submitted = modalText('[class*="submittedModal"], [id*="submittedModal"], [class*="submittedForReviewModal"], [id*="submittedForReviewModal"]');
            if (submitted) return JSON.stringify({ state: 'review', message: submitted });
            var validation = validationText('[class*="validationModal"], [id*="validationModal"]');
            if (validation && !/Validating/i.test(validation)) {
                return JSON.stringify({ state: 'validation', message: validation });
            }
            var busy = modalText('[class*="validatingModal"], [id*="validatingModal"]');
            if (busy) return JSON.stringify({ state: 'busy', message: busy });
            // Check page-level status indicators (success may show no modal at all)
            var pageText = visibleText(document.body);
            if (/Status:\s*Product is Inactive/i.test(pageText) && /in review for Category Assignment/i.test(pageText)) {
                return JSON.stringify({ state: 'review', message: 'Product is Inactive; in review for Category Assignment' });
            }
            // KO observable: productStatusCode === 'ACTV' means product is Active
            try {
                var btn = document.querySelector('[data-bind*="publishProduct"]');
                if (btn && window.ko) {
                    var vm = ko.dataFor(btn);
                    if (vm && vm.productInfo && typeof vm.productInfo === 'function') {
                        var pi = vm.productInfo();
                        if (pi && typeof pi.productStatusCode === 'function' && pi.productStatusCode() === 'ACTV') {
                            return JSON.stringify({ state: 'success', message: 'Product is Active (productStatusCode=ACTV)' });
                        }
                    }
                }
            } catch(e) {}
            // Fallback: visible "Product is Active" text on page
            var activeLabel = Array.from(document.querySelectorAll('strong.txt-green')).filter(function(el) {
                return visible(el) && /Product is Active/i.test(visibleText(el));
            });
            if (activeLabel.length) {
                return JSON.stringify({ state: 'success', message: 'Product is Active (visible label)' });
            }
            return JSON.stringify({ state: 'pending', message: '' });
        """)
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {"state": "pending", "message": safe_str(raw)}

    def make_product_active(self, timeout=90):
        """Click Make Active and wait for ASI validation/publish result."""
        logger.info("  正在执行 Make Active...")
        click_tab(self.tab, "#/product/summary")
        time.sleep(2)

        clicked = self._click_make_active()
        logger.info(f"  Make Active 触发结果: {clicked}")
        if not clicked.get("clicked"):
            if clicked.get("disabled"):
                return {"blocking": True, "message": "Make Active 按钮不可用，可能产品未保存或页面验证未通过"}
            return {"blocking": True, "message": "未找到 Make Active 按钮"}

        start = time.time()
        last_state = {}
        while time.time() - start < timeout:
            alert_text = self.handle_any_alert()
            if alert_text:
                return {"blocking": True, "message": alert_text}

            confirm = self._confirm_make_active_if_needed()
            if confirm.get("handled"):
                logger.info(f"  Make Active 确认弹窗已处理: {confirm.get('action', '')}")

            review_submit = self._submit_restricted_words_for_review_if_needed()
            if review_submit.get("handled"):
                logger.info(f"  已提交限制词产品进入审核: {review_submit.get('text', '')}")
                time.sleep(2)

            state = self._read_make_active_modal_state()
            if state != last_state:
                logger.info(f"  Make Active 状态: {state}")
                last_state = state

            status = state.get("state")
            message = safe_str(state.get("message", ""))
            if status == "success":
                logger.info("  Make Active 成功")
                self.prepare_for_next_product()
                return None
            if status == "review":
                logger.info("  Make Active 已成功提交 ASI 后台审核")
                self.prepare_for_next_product()
                return None
            if status == "validation":
                message = self.clean_make_active_validation_message(message)
                # "Product submitted successfully" 等提示实际上是成功提交审核，不是错误
                success_keywords = (
                    "submitted successfully",
                    "submitted for review",
                    "reviewed for category assignment",
                    "activated shortly",
                    "will be reviewed",
                )
                if any(kw in message.lower() for kw in success_keywords):
                    logger.info(f"  Make Active 已成功提交审核（validation modal 含成功提示）: {message}")
                    self.prepare_for_next_product()
                    return None
                return {"blocking": True, "message": message or "Make Active 验证失败"}

            time.sleep(2)

        return {"blocking": True, "message": f"Make Active 等待结果超时 ({timeout}s)"}

    # =========================
    # 主流程：处理一个产品
    # =========================

    def process_product(
        self,
        row,
        image_root_path=None,
        make_active=True,
        distributor_only_view=False,
        upload_controls=None,
        product_flags=None,
    ):
        """处理单个产品的完整流程"""
        controls = normalize_upload_controls(upload_controls)
        flags = normalize_product_flags(product_flags) if product_flags is not None else None
        required_issues = validate_required_fields(row, controls)
        if required_issues:
            message = "; ".join(required_issues)
            logger.error(f"必填字段校验失败: {message}")
            return ("fail", message[:500])

        pn = safe_str(row.get("Product_Number", "?"))
        pname = safe_str(row.get("Product_Name", ""))
        basic_row = filter_row_for_module(row, controls, "basic_details")
        pdesc = safe_str(basic_row.get("Description", ""))
        ptype = self.row_value(row, "Product_Type", "What type of product are you entering?")
        image_root_path = image_root_path or os.getcwd()

        logger.info(f"{'='*50}")
        logger.info(f"处理产品: [{pn}] {pname[:50]}")

        # 1. 导航到 Add a New Product + 创建产品。产品名和类型始终是创建所需字段。
        self.navigate_to_add_product()
        if not self.create_product(pname, pdesc, ptype):
            return ("fail", "产品创建失败")

        self.wait_product_detail_ready(timeout=35)

        # 2. Save simple fields through ASI Product API. This confirms we are
        # editing the actual Product DTO, not only changing visible DOM values.
        product_id = None
        try:
            product_id = self.get_current_product_id()
            product_json = self.get_product_json(product_id)
            if isinstance(product_json, dict):
                product_json = self.apply_basic_product_json(basic_row, product_json)
                self.save_product_json(product_json)
                logger.info(f"  基础 Product JSON 已保存: {product_id}")
            else:
                logger.warning("  Product API 返回不是 JSON 对象，跳过 JSON 保存")
        except Exception as e:
            err_msg = self.friendly_error_message(e, "Product JSON保存")
            logger.warning(f"  {err_msg}")
            # If product is in review or API returns 400, the page productId may be stale.
            # Stop early to avoid uploading images/data to the wrong product.
            if "in review" in err_msg.lower() or "cannot be saved" in err_msg.lower() or "400" in err_msg:
                return ("fail", f"{err_msg} (产品可能已提交审核，无法继续编辑)")

        # 3. 填充并保存各Tab。SKU/Availability 暂无 Excel 字段支撑，先跳过。
        module_rows = {
            "basic_details": basic_row,
            "attributes": filter_row_for_module(row, controls, "attributes"),
            "imprint": filter_row_for_module(row, controls, "imprint"),
            "pricing": filter_row_for_module(row, controls, "pricing"),
            "media": filter_row_for_module(row, controls, "media"),
        }
        tabs = []
        if module_should_run(row, controls, "basic_details"):
            tabs.append((
                "Basic Details",
                lambda: self.fill_basic_details(
                    module_rows["basic_details"],
                    distributor_only_view,
                    controls,
                    product_flags=flags,
                ),
            ))
        if module_should_run(row, controls, "attributes"):
            tabs.append(("Attributes", lambda: self.fill_attributes(module_rows["attributes"])))
        if module_should_run(row, controls, "imprint"):
            tabs.append((
                "Imprint",
                lambda: self.fill_imprint(
                    module_rows["imprint"], controls, product_flags=flags
                ),
            ))
        if module_should_run(row, controls, "pricing"):
            tabs.append((
                "Pricing",
                lambda: self.fill_pricing(module_rows["pricing"], upload_controls=controls),
            ))

        warns = []
        for tname, tfunc in tabs:
            try:
                tfunc()
                r = self.save_current_tab(tname)
                if r and r.get("blocking"):
                    return ("fail", f"{tname}: {r['message'][:100]}")
                elif r:
                    warns.append(f"{tname}: {r['message'][:80]}")
            except Exception as e:
                friendly = self.friendly_error_message(e, tname)
                logger.error(f"  {friendly}")
                return ("fail", friendly)

        if module_should_run(row, controls, "media"):
            try:
                media_result = self.fill_media(module_rows["media"], image_root_path)
                if media_result and media_result.get("blocking"):
                    return ("fail", f"Media: {media_result['message'][:100]}")
                elif media_result and media_result.get("success"):
                    logger.info(f"  Media 完成: {media_result['message']}")
                elif media_result:
                    warns.append(f"Media: {media_result['message'][:80]}")
            except Exception as e:
                friendly = self.friendly_error_message(e, "Media")
                logger.error(f"  {friendly}")
                return ("fail", friendly)
        else:
            logger.info("  Media 模块已关闭或全部字段设为跳过")

        if make_active:
            active_result = self.make_product_active()
            if active_result and active_result.get("blocking"):
                return ("fail", f"Make Active: {active_result['message'][:500]}")
            elif active_result:
                warns.append(f"Make Active: {active_result['message'][:80]}")
        else:
            logger.info("  已关闭 Make Active，本产品保存后不激活")

        if warns:
            return ("problem", "; ".join(warns))
        return ("success", "")


# =========================
# 批量处理入口
# =========================

def run_upload(excel_path, asi_id, username, password, image_root_path=None,
               browser_path=None, progress_callback=None, stop_event=None,
               make_active=True, distributor_only_view=False,
               upload_controls=None, product_flags=None):
    """批量上传入口"""
    logger.info("=" * 50)
    logger.info("ASI Product Upload 启动")

    df = read_excel(excel_path)
    image_root_path = image_root_path or str(Path(excel_path).parent)
    total = len(df)
    logger.info(f"共 {total} 个产品")

    if total == 0:
        logger.error("无产品数据")
        return

    bot = AsiBot(asi_id, username, password, browser_path=browser_path)
    bot.start_browser()

    # 通知浏览器就绪
    if progress_callback:
        progress_callback({"stage": "browser_ready", "browser_path": browser_path or ""})

    if progress_callback:
        progress_callback({"stage": "started", "total": total})

    success = fail = problem = 0
    normalized_flags = (
        normalize_product_flags(product_flags) if product_flags is not None else None
    )

    try:
        if not bot.login():
            logger.error("登录失败")
            if progress_callback:
                progress_callback({"stage": "error", "message": "登录失败，请检查账号密码"})
            return

        for seq, (idx, row) in enumerate(df.iterrows(), 1):
            # 检查停止信号
            if stop_event and stop_event.is_set():
                logger.warning("收到停止请求，中止处理")
                if progress_callback:
                    progress_callback({
                        "stage": "stopped",
                        "total": total, "processed": seq - 1,
                        "success": success, "fail": fail, "problem": problem,
                        "duration_text": "",
                    })
                return

            pn = safe_str(row.get("Product_Number", "?"))
            logger.info(f"\n[{seq}/{total}] {pn}")

            if progress_callback:
                progress_callback({
                    "stage": "processing",
                    "total": total, "processed": seq - 1,
                    "success": success, "fail": fail, "problem": problem,
                    "item_num": pn,
                })

            try:
                process_kwargs = {
                    "image_root_path": image_root_path,
                    "make_active": make_active,
                    "distributor_only_view": distributor_only_view,
                }
                if upload_controls is not None:
                    process_kwargs["upload_controls"] = upload_controls
                if normalized_flags is not None:
                    process_kwargs["product_flags"] = normalized_flags
                st, msg = bot.process_product(row, **process_kwargs)
                st = safe_str(st).lower() or "fail"
                logger.info(f"  产品处理结果: Process={st}, Process status={msg or ''}")
                write_result_to_excel(excel_path, idx, st, msg)
                bot.prepare_for_next_product()

                if st == "success":
                    success += 1
                elif st == "problem":
                    problem += 1
                else:
                    fail += 1
            except Exception as e:
                fail += 1
                logger.exception(f"异常: {e!r}")
                write_result_to_excel(excel_path, idx, "fail", str(e)[:200])
                # 强制重置浏览器状态，防止影响下一个产品
                bot.prepare_for_next_product()
                try:
                    bot.tab.run_js("window.location.hash = '#/dashboard';")
                except Exception:
                    pass
                time.sleep(3)
                bot._dismiss_all_alerts()

            if progress_callback:
                progress_callback({
                    "stage": "row_done",
                    "total": total, "processed": seq,
                    "success": success, "fail": fail, "problem": problem,
                    "item_num": pn,
                })

            time.sleep(5)
    finally:
        bot.close()

    logger.info("=" * 50)
    logger.info(f"完成! 总计{total} | 成功{success} | 问题{problem} | 失败{fail}")
    logger.info(f"结果: {excel_path}")

    if progress_callback:
        progress_callback({
            "stage": "finished",
            "total": total, "processed": total,
            "success": success, "fail": fail, "problem": problem,
            "duration_text": "",
        })

print("asi_bot.py 加载完成")
