"""
ASI Product Upload - 工具函数库
参考 sagemember/tools.py 架构，适配 ASI 平台 (Knockout.js SPA)
"""
import math
import time
import re
import json
import zipfile
import os
import sys
from pathlib import Path

import pandas as pd
from DrissionPage.common import Keys
from loguru import logger
from upload_controls import validate_required_fields


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")


def client_storage_base():
    """跨平台返回客户端数据根目录（Windows: %LOCALAPPDATA%，macOS: ~/Library/Application Support）。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("LOCALAPPDATA") or Path.home())


ASI_OPTIONS_CACHE_FILE = (
    client_storage_base() / "ASIProductUpload" / "Client" / "asi_options.json"
)


class LicenseExpiredError(RuntimeError):
    """当网页结构发生变化（DOM 选择器失效）时抛出此异常。"""
    pass


# =========================
# 1. 基础工具函数
# =========================

def is_nan(value):
    try:
        return pd.isna(value)
    except Exception:
        return False


def safe_str(value):
    """安全转换为字符串，处理 NaN / None / float"""
    if value is None or is_nan(value):
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer():
            return str(int(value))
    return str(value).strip()


def normalize_punctuation(value):
    """Normalize common Chinese punctuation used in structured Excel fields."""
    text = safe_str(value)
    if not text:
        return ""
    translations = {
        "，": ",",
        "、": ",",
        "；": ";",
        "：": ":",
        "／": "/",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "－": "-",
        "–": "-",
        "—": "-",
        "×": "x",
    }
    for source, target in translations.items():
        text = text.replace(source, target)
    return text


def split_multi_value(value, seps=(",", ";", "|", "/")):
    """将逗号/分号分隔的多值字符串拆分为列表"""
    text = normalize_punctuation(value)
    if not text:
        return []
    for sep in seps:
        text = text.replace(sep, ",")
    return [x.strip() for x in text.split(",") if x.strip()]


def get_product_image_folder(image_root_path, product_number):
    """Return the image folder for a product number, if it exists."""
    root = Path(safe_str(image_root_path))
    product_no = safe_str(product_number)
    if not root or not product_no:
        return ""
    if not root.is_dir():
        logger.warning(f"图片根目录不存在，跳过图片上传: {root}")
        return ""

    direct = root / product_no
    if direct.is_dir():
        return str(direct)

    # Some rows use lowercase product numbers while folders may be mixed case.
    product_lower = product_no.lower()
    for child in root.iterdir():
        if child.is_dir() and child.name.lower() == product_lower:
            return str(child)

    logger.warning(f"未找到产品图片目录，跳过图片上传: {direct}")
    return ""


def iter_image_files(folder_path):
    """Yield product image files in leading-number upload order."""
    folder = Path(safe_str(folder_path))
    if not folder.is_dir():
        return

    def image_sort_key(path):
        name = path.name.lower()
        match = re.match(r"^\s*(\d+)(?:[_\-\s.]|$)", name)
        if match:
            return (0, int(match.group(1)), name)
        return (1, 0, name)

    for path in sorted(folder.iterdir(), key=image_sort_key):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            yield str(path)


# =========================
# 2. 浏览器操作工具
# =========================

def get_ele(tab, locator, timeout=5):
    """获取元素，带超时和异常处理"""
    try:
        return tab.ele(locator, timeout=timeout)
    except Exception:
        return None


def fill_input(tab, locator, value):
    """填充普通 input 字段"""
    value = safe_str(value)
    if not value:
        return False
    try:
        ele = tab.ele(locator, timeout=5)
        if ele:
            ele.clear()
            ele.input(value)
            time.sleep(0.3)
            return True
    except Exception as e:
        logger.warning(f"fill_input 失败 [{locator}]: {e}")
    return False


def fill_textarea(tab, locator, value):
    """?? textarea ??"""
    value = safe_str(value)
    if not value:
        return False
    try:
        ele = tab.ele(locator, timeout=5)
        if ele:
            ele.click()
            time.sleep(0.3)
            ele.clear()
            ele.input(value)
            time.sleep(0.3)
            return True
    except Exception as e:
        pass
    
    # ??: ?JS????
    try:
        # ? @id=xxx ???? id
        import re as _re
        m = _re.search(r"@id=([\w-]+)", locator)
        if m:
            el_id = m.group(1)
            tab.run_js(f"""
                var el = document.getElementById("{el_id}");
                if (el) {{
                    el.value = {repr(value)};
                    el.dispatchEvent(new Event("change", {{bubbles: true}}));
                    el.dispatchEvent(new Event("input", {{bubbles: true}}));
                }}
            """)
            time.sleep(0.3)
            return True
    except Exception as e:
        logger.warning(f"fill_textarea ?? [{locator}]: {e}")
    return False


def fill_select_by_text(tab, locator, text):
    """通过选项文本选择下拉框"""
    text = safe_str(text)
    if not text:
        return False
    try:
        ele = tab.ele(locator, timeout=5)
        if ele:
            ele.select.by_text(text)
            time.sleep(0.3)
            return True
    except Exception as e:
        logger.warning(f"fill_select_by_text 失败 [{locator}]: {e}")
    return False


def fill_select_by_value(tab, locator, value):
    """通过选项值选择下拉框（使用JS触发change事件）"""
    value = safe_str(value)
    if not value:
        return False
    try:
        result = tab.run_js(f"""
            var sel = document.querySelector('{locator}');
            if (!sel) return false;
            for (var i = 0; i < sel.options.length; i++) {{
                if (sel.options[i].value === {repr(value)} || 
                    sel.options[i].textContent.trim() === {repr(value)}) {{
                    sel.selectedIndex = i;
                    sel.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return true;
                }}
            }}
            return false;
        """)
        if result:
            time.sleep(0.5)
            return True
    except Exception as e:
        logger.warning(f"fill_select_by_value 失败 [{locator}]: {e}")
    return False


def fill_token_input(tab, token_input_id, values):
    """
    填充 token-input 字段（标签输入框）
    ASI 使用 token-input 插件，需要：
    1. 定位到 token-input-xxx 的 input 元素
    2. 逐个输入值，按回车确认
    """
    if isinstance(values, str):
        values = split_multi_value(values)
    if not values:
        return False

    try:
        for val in values:
            val = val.strip()
            if not val:
                continue
            tab.run_js(f"""
                var input = document.getElementById('token-input-{token_input_id}');
                if (!input) {{
                    // 尝试查找 li.token-input-input-token-facebook 下的 input
                    var container = document.getElementById('{token_input_id}');
                    if (container) {{
                        var li = container.parentElement.querySelector('li.token-input-input-token-facebook input');
                        if (li) input = li;
                    }}
                }}
                if (input) {{
                    input.value = {repr(val)};
                    input.focus();
                    // 触发 keydown 模拟回车
                    var e = new KeyboardEvent('keydown', {{
                        keyCode: 13, which: 13, key: 'Enter',
                        bubbles: true, cancelable: true
                    }});
                    input.dispatchEvent(e);
                }}
            """)
            time.sleep(0.3)
        return True
    except Exception as e:
        logger.warning(f"fill_token_input 失败 [{token_input_id}]: {e}")
    return False


def fill_field_js(tab, bind_value, value):
    """
    通过 Knockout data-bind 的 value 绑定名直接设置值
    适用于没有固定 id 但知道绑定名称的字段
    """
    value = safe_str(value)
    if not value:
        return False
    try:
        tab.run_js(f"""
            var els = document.querySelectorAll('[data-bind]');
            for (var i = 0; i < els.length; i++) {{
                var bind = els[i].getAttribute('data-bind') || '';
                if (bind.indexOf('value: {bind_value}') !== -1) {{
                    if (els[i].tagName === 'TEXTAREA' || els[i].tagName === 'INPUT') {{
                        els[i].value = {repr(value)};
                        els[i].dispatchEvent(new Event('change', {{bubbles: true}}));
                        return true;
                    }}
                }}
            }}
            return false;
        """)
        time.sleep(0.3)
        return True
    except Exception as e:
        logger.warning(f"fill_field_js 失败 [{bind_value}]: {e}")
    return False


def click_by_js(tab, selector):
    """通过 JS 点击元素（绕过可见性检查）"""
    try:
        selector_json = json.dumps(selector)
        tab.run_js(f"""
            var el = document.querySelector({selector_json});
            if (el) {{ el.click(); return true; }}
            return false;
        """)
        time.sleep(0.5)
        return True
    except Exception as e:
        logger.warning(f"click_by_js 失败 [{selector}]: {e}")
    return False


def is_transient_page_refresh_error(exc):
    """Return True for DrissionPage errors raised while the page is reloading."""
    text = safe_str(exc).lower()
    refresh_terms = (
        "页面被刷新",
        "page was refreshed",
        "page refreshed",
        "page is refreshing",
        "加载完成",
    )
    return any(term in text for term in refresh_terms)


def click_tab(tab, hash_url, retries=3, retry_delay=2):
    """点击产品详情页的 Tab 导航"""
    attempts = max(1, int(retries or 1))
    for attempt in range(1, attempts + 1):
        try:
            result = tab.run_js(f"""
                var links = document.querySelectorAll('li.top-route a, li.top-route-tab a');
                for (var i = 0; i < links.length; i++) {{
                    var href = links[i].getAttribute('data-href');
                    if (href === '{hash_url}') {{
                        links[i].click();
                        return true;
                    }}
                }}
                return false;
            """)
            if result:
                time.sleep(3)
                return True
            if attempt < attempts:
                logger.debug(f"click_tab 未找到目标 Tab，重试 {attempt}/{attempts} [{hash_url}]")
                time.sleep(retry_delay)
                continue
            return False
        except Exception as e:
            if attempt < attempts and is_transient_page_refresh_error(e):
                logger.warning(f"click_tab 页面刷新中，重试 {attempt}/{attempts} [{hash_url}]: {e}")
                time.sleep(retry_delay)
                continue
            logger.warning(f"click_tab 失败 [{hash_url}]: {e}")
            return False
    return False


# =========================
# 3. 保存结果处理
# =========================

def classify_save_alert(alert_text, default_blocking=True):
    """Classify a save confirmation or validation message."""
    text = safe_str(alert_text)
    if not text:
        return {"blocking": False, "message": ""}

    lowered = text.lower()
    uppered = text.upper()
    if "SUCCESS" in uppered or "saved" in lowered or "保存成功" in text:
        if "but" in lowered or "warning" in lowered or "警告" in text:
            return {"blocking": False, "message": text}
        return None

    blocking_terms = (
        "VALIDATION",
        "required",
        "invalid",
        "not valid",
        "cannot",
        "can't",
        "must",
        "please",
        "error",
        "错误",
        "必填",
        "无效",
    )
    if any(term.lower() in lowered for term in blocking_terms):
        return {"blocking": True, "message": text}

    return {"blocking": default_blocking, "message": text}


def wait_and_handle_alert(tab, timeout=30):
    """
    等待 Save 后的 alert 弹窗出现并处理
    返回: None=成功, {"blocking": True/False, "message": str}
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            states = getattr(tab, "states", None)
            if states is not None and getattr(states, "has_alert", False):
                alert_text = safe_str(tab.handle_alert(accept=True, timeout=1))
                logger.info(f"浏览器 alert: {alert_text}")
                result = classify_save_alert(alert_text, default_blocking=False)
                if result is not None and result.get("message"):
                    return result
                return None
        except Exception as e:
            logger.warning(f"处理浏览器 alert 失败: {e}")

        try:
            # 检查 alert 弹窗
            alert_text = tab.run_js("""
                var alertEl = document.querySelector('.modal.fade.in .modal-body');
                if (alertEl && alertEl.offsetParent) {
                    return alertEl.textContent.trim();
                }
                // 也检查 sayModalSuccessDialog
                var successEl = document.getElementById('sayModalSuccessDialog');
                if (successEl && successEl.offsetParent && 
                    successEl.classList.contains('in')) {
                    return 'SUCCESS: ' + (successEl.querySelector('.modal-body')?.textContent?.trim() || '');
                }
                // 检查 validation modal
                var validationEl = document.querySelector('[id*="validationModal"]');
                if (validationEl && validationEl.offsetParent &&
                    validationEl.classList.contains('in')) {
                    return 'VALIDATION: ' + validationEl.textContent.trim().substring(0, 200);
                }
                var savedText = Array.from(document.querySelectorAll('body *'))
                    .filter(function(el) { return !!(el.offsetParent || el.getClientRects().length); })
                    .map(function(el) { return (el.textContent || '').trim(); })
                    .find(function(text) {
                        return text === 'Data saved successfully' ||
                               text.indexOf('Data saved successfully') !== -1 ||
                               text.indexOf('Product saved successfully') !== -1;
                    });
                if (savedText) return 'SUCCESS: ' + savedText;
                return '';
            """) or ""
            
            if alert_text:
                # 尝试关闭弹窗
                tab.run_js("""
                    var okBtn = document.querySelector('.modal.fade.in .btn-primary');
                    if (!okBtn) okBtn = document.querySelector('#sayModalSuccessDialog .btn-primary');
                    if (!okBtn) okBtn = document.querySelector('[id*="validationModal"] .btn-primary');
                    if (!okBtn) okBtn = document.querySelector('.modal.fade.in .btn');
                    if (okBtn) okBtn.click();
                """)
                time.sleep(1)
                return classify_save_alert(alert_text, default_blocking=True)
        except Exception:
            pass
        
        time.sleep(1)
    
    logger.warning(f"等待 alert 超时 ({timeout}s)")
    return {"blocking": False, "message": f"等待确认超时 ({timeout}s)"}


def click_save_button(tab, timeout=8, log_missing=True):
    """Click the visible Save button on the current product tab."""
    time.sleep(2)
    
    # Use JS to find the active visible Save button.
    result = tab.run_js("""
        var btns = document.querySelectorAll('button');
        var found = [];
        for (var i = 0; i < btns.length; i++) {
            var b = btns[i];
            var text = (b.textContent || '').trim();
            var bind = b.getAttribute('data-bind') || '';
            var disabled = b.disabled || b.classList.contains('disabled');
            var visible = !!(b.offsetParent || b.getClientRects().length);
            var isSave = (text === 'Save' || text === 'Bulk Save' || 
                          bind.indexOf('saveProduct') !== -1 ||
                          bind.indexOf('savePricing') !== -1 ||
                          bind.indexOf('saveImprint') !== -1 ||
                          bind.indexOf('saveMedia') !== -1);
            if (isSave && visible && !disabled) {
                found.push({text: text, bind: bind.substring(0, 80), visible: visible, disabled: disabled});
            }
        }
        return JSON.stringify(found);
    """)
    
    try:
        import json as _json
        btns_found = _json.loads(result or "[]")
        logger.info(f"    找到 {len(btns_found)} 个可点击 Save 按钮: {btns_found}")
    except Exception:
        btns_found = []
    
    if btns_found:
        # Click the first visible enabled Save button.
        tab.run_js("""
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                var b = btns[i];
                var text = (b.textContent || '').trim();
                var bind = b.getAttribute('data-bind') || '';
                var visible = !!(b.offsetParent || b.getClientRects().length);
                var disabled = b.disabled || b.classList.contains('disabled');
                var isSave = (text === 'Save' || text === 'Bulk Save' || 
                              bind.indexOf('saveProduct') !== -1 ||
                              bind.indexOf('savePricing') !== -1 ||
                              bind.indexOf('saveImprint') !== -1 ||
                              bind.indexOf('saveMedia') !== -1);
                if (isSave && visible && !disabled) {
                    b.click();
                    return 'clicked: ' + text;
                }
            }
            return 'no save button clicked';
        """)
        return True
    
    if log_missing:
        logger.warning("  未找到 Save 按钮")
    return False

# =========================
# 4. Excel 读写
# =========================

def read_excel(excel_path):
    """读取 Excel 产品数据"""
    try:
        df = pd.read_excel(excel_path, sheet_name="ASI制作表格", header=3)
    except ValueError:
        df = pd.read_excel(excel_path, sheet_name=0, header=3)
    except zipfile.BadZipFile:
        raise ValueError(
            f"Excel 文件损坏或格式无效: '{excel_path}'。"
            "文件可能不完整或被截断，请重新获取该文件。"
        )

    alias_map = {
        "Product Number": "Product_Number",
        "Product Name": "Product_Name",
        "Summary Description": "Summary",
    }
    for source, target in alias_map.items():
        if target not in df.columns and source in df.columns:
            df[target] = df[source]

    process_col = None
    legacy_process_status_col = None
    for col in df.columns:
        normalized = safe_str(col).replace(" ", "").lower()
        if normalized == "process":
            process_col = col
            break
        if normalized == "processstatus":
            legacy_process_status_col = col
    if process_col is None:
        process_col = legacy_process_status_col

    def should_process(value):
        status = safe_str(value).strip().lower()
        return status != 'success'

    # 过滤掉产品编号为空的行
    df = df[df["Product_Number"].apply(lambda value: bool(safe_str(value)))]
    if process_col is not None:
        df = df[df[process_col].apply(should_process)]
    return df


def write_result_to_excel(excel_path, row_index, status, message=""):
    """将处理结果写回 Excel"""
    from openpyxl import load_workbook
    for attempt in range(3):
        try:
            wb = load_workbook(excel_path)
            ws = wb["ASI制作表格"]
            
            # Current workbook uses row 4 as the program header row.
            headers_row2 = [cell.value for cell in ws[2]]
            headers_row3 = [cell.value for cell in ws[3]]
            headers_row4 = [cell.value for cell in ws[4]]
            process_col = None
            legacy_process_status_col = None
            message_col = None

            for i, h in enumerate(headers_row4, 1):
                normalized = safe_str(h).replace(" ", "").lower()
                if normalized == "process":
                    process_col = i
                elif normalized == "processstatus":
                    legacy_process_status_col = i
                    if message_col is None:
                        message_col = i
                elif normalized == "processmessage":
                    message_col = i

            for i, h in enumerate(headers_row3, 1):
                normalized = safe_str(h).replace(" ", "").lower()
                if process_col is None and normalized == "process":
                    process_col = i
                elif normalized == "processstatus":
                    if legacy_process_status_col is None:
                        legacy_process_status_col = i
                    if message_col is None:
                        message_col = i
                elif message_col is None and normalized == "processmessage":
                    message_col = i

            for i, h in enumerate(headers_row2, 1):
                normalized = safe_str(h).replace(" ", "").lower()
                if process_col is None and normalized == "process":
                    process_col = i
                elif normalized == "processstatus":
                    if legacy_process_status_col is None:
                        legacy_process_status_col = i
                    if message_col is None:
                        message_col = i
                elif message_col is None and normalized == "processmessage":
                    message_col = i

            if process_col is None and legacy_process_status_col is not None:
                process_col = legacy_process_status_col
                message_col = None

            if process_col is not None:
                for header_row in (2, 3, 4):
                    ws.cell(row=header_row, column=process_col, value="Process")
            if message_col is not None and message_col != process_col:
                for header_row in (2, 3, 4):
                    ws.cell(row=header_row, column=message_col, value="Process status")
            
            if process_col is None:
                process_col = ws.max_column + 1
                ws.cell(row=2, column=process_col, value="Process")
                ws.cell(row=3, column=process_col, value="Process")
                ws.cell(row=4, column=process_col, value="Process")
            if message_col is None or message_col == process_col:
                message_col = ws.max_column + 1
                ws.cell(row=2, column=message_col, value="Process status")
                ws.cell(row=3, column=message_col, value="Process status")
                ws.cell(row=4, column=message_col, value="Process status")
            
            # row_index 从0开始，Excel数据从第5行开始(第4行英文列名)
            excel_row = row_index + 5
            ws.cell(row=excel_row, column=process_col, value=status)
            ws.cell(row=excel_row, column=message_col, value=message[:500] if message else "")
            
            wb.save(excel_path)
            wb.close()
            return True
        except PermissionError:
            logger.warning(f"Excel被占用，第{attempt+1}次重试...")
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            logger.error(f"Excel写入失败: {e}")
            if attempt < 2:
                time.sleep(2)
    return False

import re as _re
import json
from pathlib import Path


def load_asi_options(json_path=None):
    """Load ASI options and validation rules from JSON config file."""
    if json_path is None:
        if ASI_OPTIONS_CACHE_FILE.is_file():
            try:
                with open(ASI_OPTIONS_CACHE_FILE, "r", encoding="utf-8-sig") as f:
                    cached = json.load(f)
                if isinstance(cached, dict) and isinstance(cached.get("validation_rules"), dict):
                    return cached
                logger.warning("自动更新的 ASI 候选值缓存结构无效，改用软件内置配置。")
            except Exception as exc:
                logger.warning(f"读取自动更新的 ASI 候选值失败，改用软件内置配置: {exc}")
        json_path = Path(__file__).parent / "asi_options.json"
    with open(json_path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _find_excel_col(row, *column_names):
    """Find a cell value by one or more possible column names, with fuzzy matching."""
    for name in column_names:
        val = safe_str(row.get(name, ""))
        if val:
            return val
    normalized_lookup = {}
    for key in row.index:
        nk = _re.sub(r"[^a-z0-9]+", "", safe_str(key).lower())
        if nk and nk not in normalized_lookup:
            normalized_lookup[nk] = key
    for name in column_names:
        nn = _re.sub(r"[^a-z0-9]+", "", safe_str(name).lower())
        if nn in normalized_lookup:
            val = safe_str(row.get(normalized_lookup[nn], ""))
            if val:
                return val
    return ""


def _norm_match(s):
    """Normalize string for case-insensitive fuzzy comparison."""
    return safe_str(s).strip().lower()


def _split_commas(value):
    """Split a value by Chinese or English commas into a list of trimmed tokens."""
    if not value:
        return []
    text = safe_str(value).replace("\uff0c", ",").replace("\u3001", ",")
    return [x.strip() for x in text.split(",") if x.strip()]


# ---------------------------------------------------------------------------
# Core rule engine
# ---------------------------------------------------------------------------

def _eval_regex_rule(row, columns, pattern, message):
    """Validate columns against a regex pattern."""
    errors = []
    for col in columns:
        val = safe_str(row.get(col, ""))
        if not val:
            continue
        if not _re.match(pattern, val):
            errors.append(f"第{col}列: '{val}' {message}")
    return errors


def _eval_max_length_rule(row, columns, max_val):
    """Validate max character length."""
    errors = []
    for col in columns:
        val = safe_str(row.get(col, ""))
        if not val or len(val) <= max_val:
            continue
        errors.append(f"第{col}列: {len(val)}个字符超过{max_val}个字符上限")
    return errors


def _eval_max_items_rule(row, columns, max_val):
    """Validate max number of comma-separated items."""
    errors = []
    for col in columns:
        val = _find_excel_col(row, col)
        if not val:
            continue
        items = _split_commas(val)
        if len(items) > max_val:
            errors.append(f"第{col}列: {len(items)}个项目超过上限{max_val}个 ({val})")
    return errors


def _eval_in_options_rule(row, columns, options_key, options_data, is_multi=False):
    """Validate values against a reference options list."""
    errors = []
    opt_def = options_data.get(options_key, {})
    raw_opts = opt_def.get("options", [])

    option_set = set()
    if raw_opts and isinstance(raw_opts[0], dict):
        for o in raw_opts:
            option_set.add(_norm_match(o.get("value", o.get("label", ""))))
            option_set.add(_norm_match(o.get("label", "")))
    else:
        option_set = {_norm_match(o) for o in raw_opts}

    if not option_set:
        return errors

    for col in columns:
        val = _find_excel_col(row, col)
        if not val:
            continue
        if is_multi:
            for item in _split_commas(val):
                if _norm_match(item) not in option_set:
                    sug = _fuzzy_suggest(item, option_set)
                    errors.append(f"{col}: '{item}' 不在可选项中 (接近: {sug})")
        else:
            if _norm_match(val) not in option_set:
                sug = _fuzzy_suggest(val, option_set)
                errors.append(f"{col}: '{val}' 不在可选项中 (接近: {sug})")
    return errors


def _fuzzy_suggest(value, option_set):
    """Return up to 3 closest-matching options."""
    clean = _re.sub(r"[^a-z0-9]", "", _norm_match(value))
    matches = [o for o in option_set if _re.sub(r"[^a-z0-9]", "", o) == clean]
    return ", ".join(matches[:3]) if matches else "无"


def _eval_conditional_rule(row, columns, depends_on, cases):
    """Validate based on another column's value (e.g. Size_Values depends on Select Size)."""
    errors = []
    dep_val = _find_excel_col(row, depends_on)
    if not dep_val:
        return errors

    dep_norm = _norm_match(dep_val)
    matched = None
    for case in cases:
        when = case.get("when", "")
        if _norm_match(when) == dep_norm or when == dep_val:
            matched = case
            break
        if _re.sub(r"[^a-z0-9]", "", _norm_match(when)) == _re.sub(r"[^a-z0-9]", "", dep_norm):
            matched = case
            break

    if matched:
        pattern = matched.get("pattern", "")
        for col in columns:
            val = safe_str(row.get(col, ""))
            if not val:
                continue
            if not _re.match(pattern, val):
                errors.append(f"第{col}列: '{val}' 应为: {matched.get('message', '')}")
    return errors


def _eval_location_subset_rule(row, columns, depends_on):
    """Validate that locations in Set-up Change are a subset of Imprint Location."""
    errors = []
    for col in columns:
        val = _find_excel_col(row, col)
        dep_val = _find_excel_col(row, depends_on)
        if not val or not dep_val:
            continue

        setup_locs = set()
        for pair in _split_commas(val):
            if ":" in pair:
                setup_locs.add(pair.split(":")[0].strip().lower())

        allowed = set(x.strip().lower() for x in _split_commas(dep_val))
        violators = setup_locs - allowed
        if violators:
            errors.append(f"第{col}列: 位置 {violators} 不在 Imprint Location 已选项中 ({list(allowed)})")
    return errors


_RULE_DISPATCH = {
    "regex":              _eval_regex_rule,
    "max_length":         _eval_max_length_rule,
    "max_items":          _eval_max_items_rule,
    "in_options":         _eval_in_options_rule,
    "conditional":        _eval_conditional_rule,
    "location_subset_of": _eval_location_subset_rule,
}


def _validate_rule(row, rule_def, options_data):
    """Execute all sub-rules for one validation rule. Returns list of error strings."""
    errors = []
    columns = rule_def.get("excel_columns", [])
    sub_rules = rule_def.get("rules", [])

    for sub in sub_rules:
        rule_type = sub.get("type", "")
        handler = _RULE_DISPATCH.get(rule_type)
        if handler is None:
            continue
        try:
            if rule_type == "regex":
                e = handler(row, columns, sub.get("pattern", ""), sub.get("message", "format error"))
            elif rule_type == "max_length":
                e = handler(row, columns, sub.get("value", 0))
            elif rule_type == "max_items":
                e = handler(row, columns, sub.get("value", 0))
            elif rule_type == "in_options":
                e = handler(row, columns, sub.get("options_key", ""), options_data, sub.get("multi", False))
            elif rule_type == "conditional":
                e = handler(row, columns, sub.get("depends_on", ""), sub.get("cases", []))
            elif rule_type == "location_subset_of":
                e = handler(row, columns, sub.get("depends_on", ""))
            else:
                e = []
            errors.extend(e)
        except Exception as exc:
            errors.append(f"规则执行错误: {exc}")
    return errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_product_data(df, options_path=None, upload_controls=None):
    """Validate Excel data against ASI options from JSON config.

    Returns: list of dicts with product_number, row_index, issues
    """
    options = load_asi_options(options_path)
    rules = options.get("validation_rules", {})
    warnings = []

    for idx, row in df.iterrows():
        issues = []
        pn = safe_str(row.get("Product_Number", "?"))

        issues.extend(validate_required_fields(row, upload_controls))

        for rule_key, rule_def in rules.items():
            try:
                rule_errors = _validate_rule(row, rule_def, options)
                issues.extend(rule_errors)
            except Exception as e:
                issues.append(f"[{rule_def.get('label', rule_key)}] error: {e}")

        # Also check for previously-failed rows
        for col in row.index:
            if _re.sub(r"[^a-z0-9]+", "", safe_str(col).lower()) == "process":
                if safe_str(row.get(col, "")).strip().lower() == "fail":
                    issues.append("之前处理失败，这次将重试")
                break

        if issues:
            warnings.append({
                "product_number": pn,
                "row_index": idx,
                "issues": issues,
            })

    return warnings


def run_precheck(excel_path, options_path=None, upload_controls=None):
    """Run full pre-check on Excel data and write issues to ProcessMessage column.

    Returns: dict with total, problems, clean, warnings
    """
    from openpyxl import load_workbook

    df = read_excel(excel_path)
    warnings = validate_product_data(df, options_path, upload_controls=upload_controls)

    wb = load_workbook(excel_path)
    ws = wb["ASI制作表格"]

    headers_row4 = [cell.value for cell in ws[4]]
    headers_row3 = [cell.value for cell in ws[3]]
    message_col = None
    process_col = None

    for i, h in enumerate(headers_row4, 1):
        n = safe_str(h).replace(" ", "").lower()
        if n in ("processmessage", "processstatus"):
            message_col = i
        if n == "process":
            process_col = i

    if message_col is None:
        for i, h in enumerate(headers_row3, 1):
            n = safe_str(h).replace(" ", "").lower()
            if n in ("processmessage", "processstatus"):
                message_col = i
            if process_col is None and n == "process":
                process_col = i

    issues_map = {w["row_index"]: w["issues"] for w in warnings}

    for excel_row in range(5, ws.max_row + 1):
        df_idx = excel_row - 5
        if df_idx in issues_map:
            msg = "; ".join(issues_map[df_idx])[:500]
            if message_col:
                ws.cell(row=excel_row, column=message_col, value=msg)
            if process_col:
                current = safe_str(ws.cell(row=excel_row, column=process_col).value)
                if current.lower() != "success":
                    ws.cell(row=excel_row, column=process_col, value="problem")

    try:
        wb.save(excel_path)
    except PermissionError:
        raise PermissionError(
            f"Excel file is locked by another program. Please close it first.\n{excel_path}"
        )

    total = len(df)
    problem_count = len(warnings)
    return {
        "total": total,
        "problems": problem_count,
        "clean": total - problem_count,
        "warnings": warnings,
    }
