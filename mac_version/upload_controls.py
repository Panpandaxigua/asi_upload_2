# -*- coding: utf-8 -*-
"""Upload policy shared by the UI, precheck, and ASI automation layers."""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping


MODE_AUTO = "auto"
MODE_REQUIRED = "required"
MODE_SKIP = "skip"
FIELD_MODES = (MODE_AUTO, MODE_REQUIRED, MODE_SKIP)


def _price_tier_columns() -> tuple[str, ...]:
    columns = []
    for index in range(1, 9):
        columns.extend((f"Q{index}", f"P{index}"))
    return tuple(columns)


UPLOAD_CONTROL_SCHEMA = {
    "basic_details": {
        "label": "Basic Details",
        "description": "描述、分类、主题、产地、运输和可见范围",
        "default_enabled": True,
        "fields": {
            "description": {
                "label": "产品描述",
                "columns": ("Description", "Product Description"),
            },
            "summary": {
                "label": "概要",
                "columns": ("Summary", "Summary Description", "Product Summary"),
            },
            "category": {"label": "分类", "columns": ("Category",)},
            "keywords": {"label": "关键词", "columns": ("Keywords",)},
            "themes": {
                "label": "主题",
                "columns": ("Theme", "Product Theme"),
            },
            "origins": {
                "label": "生产、组装和装饰产地",
                "columns": ("Manufactured In", "Assembled In", "Decorated In"),
            },
            "shipping": {
                "label": "运输与包装",
                "columns": (
                    "Number of Items",
                    "Shipping Dimensions L",
                    "Shipping Dimensions W",
                    "Shipping Dimensions H",
                    "Shipping Weight",
                    "Shipper Bills by",
                    "Item Packaging",
                    "Additional Shipping Information",
                ),
            },
            "prop65": {
                "label": "Prop 65 无化学品声明",
                "columns": (),
                "runtime": True,
                "runtime_switch": True,
                "default_value": True,
            },
            "certifications": {
                "label": "认证与合规",
                "columns": ("Comp_Cert", "Certifications and Compliance"),
            },
            "distributor_only_view": {
                "label": "Distributor Only View",
                "columns": (),
                "runtime": True,
            },
        },
    },
    "attributes": {
        "label": "Attributes",
        "description": "颜色、尺寸、材料、形状和重量",
        "default_enabled": True,
        "fields": {
            "colors": {
                "label": "产品颜色",
                "columns": ("Product_Color", "Product Color"),
            },
            "size": {
                "label": "产品尺寸",
                "columns": ("Size_Values", "Size Values", "Select Size"),
            },
            "materials": {
                "label": "材料和自定义材料名",
                "columns": (
                    "Material",
                    "Material Custom Name",
                    "Material_Custom_Name",
                    "Material Alias",
                ),
            },
            "shapes": {
                "label": "形状",
                "columns": (
                    "Shapes",
                    "Shape",
                    "Search Filter Option",
                    "Shapes Search Filter",
                    "Any Shape/Custom Shape",
                    "Shape Options",
                ),
            },
            "weight": {
                "label": "Item Weight",
                "columns": ("Weight", "Item Weight"),
            },
        },
    },
    "imprint": {
        "label": "Imprint",
        "description": "印刷方式、配色、生产时间、位置和稿件服务",
        "default_enabled": True,
        "fields": {
            "unimprinted": {
                "label": "Unimprinted 默认选项",
                "columns": (),
                "runtime": True,
                "runtime_switch": True,
                "default_value": True,
            },
            "methods": {
                "label": "印刷方式",
                "columns": ("Select Method", "Imprint_Method", "Imprint Method"),
            },
            "personalization": {
                "label": "个性化定制",
                "columns": ("Personalization Available", "Personalization_Available"),
            },
            "imprint_color_options": {
                "label": "压印配色选项",
                "columns": ("Imprint Colors", "Imprint_Color", "Imprint Color"),
            },
            "colors": {
                "label": "印刷颜色",
                "columns": ("Colors", "Imprint Color Names"),
            },
            "production_time": {
                "label": "生产时间",
                "columns": ("Production time", "Production Time"),
            },
            "rush_service": {
                "label": "Rush Service",
                "columns": ("Rush Service Offered", "Rush_Service_Offered"),
            },
            "same_day_service": {
                "label": "Same Day Service",
                "columns": ("Same Day Service Offered", "Same_Day_Service_Offered"),
            },
            "imprint_size": {
                "label": "印刷尺寸",
                "columns": ("Imprint Size", "Imprint_Size"),
            },
            "imprint_location": {
                "label": "印刷位置",
                "columns": ("Imprint Location", "Imprint_Location"),
            },
            "additional_colors": {
                "label": "附加颜色说明",
                "columns": (
                    "Additional Colors Information",
                    "Additional_Colors_Information",
                ),
            },
            "artwork": {
                "label": "Artwork Information",
                "columns": ("Artwork Information", "Artwork_Information"),
            },
        },
    },
    "pricing": {
        "label": "Pricing",
        "description": "价格档位、折扣代码、价格说明和安装费",
        "default_enabled": True,
        "fields": {
            "price_tiers": {
                "label": "价格档位",
                "columns": _price_tier_columns(),
                "validator": "price_tiers",
            },
            "price_codes": {
                "label": "Price Codes",
                "columns": ("Price Codes", "Price_Codes"),
            },
            "price_includes": {
                "label": "Price Includes",
                "columns": ("Price Includes", "Price_Includes"),
            },
            "setup_charges": {
                "label": "Set-up Charge",
                "columns": (
                    "Set-up Change",
                    "Set-up Charge",
                    "Setup Charge",
                    "Set-up Change Codes",
                    "Set-up Change Code",
                    "Set-up Charge Codes",
                    "Set-up Charge Code",
                    "Setup Charge Codes",
                    "Setup Charge Code",
                    "Imprint Location",
                    "Imprint_Location",
                ),
            },
            "order_less_than_minimum": {
                "label": "允许低于最低数量下单",
                "columns": (),
                "runtime": True,
                "runtime_switch": True,
                "default_value": True,
            },
        },
    },
    "media": {
        "label": "Media",
        "description": "图片上传以及图片和产品颜色的关联",
        "default_enabled": True,
        "fields": {
            "images": {
                "label": "上传产品图片",
                "columns": (),
                "runtime": True,
            },
            "color_matching": {
                "label": "按文件名匹配颜色",
                "columns": ("Product_Color", "Product Color"),
            },
        },
    },
}


def default_upload_controls() -> dict[str, Any]:
    modules = {}
    for module_key, module in UPLOAD_CONTROL_SCHEMA.items():
        modules[module_key] = {
            "enabled": bool(module.get("default_enabled", True)),
            "fields": {
                field_key: field.get("default_mode", MODE_AUTO)
                for field_key, field in module["fields"].items()
            },
            "values": {
                field_key: bool(field.get("default_value", False))
                for field_key, field in module["fields"].items()
                if field.get("runtime_switch")
            },
        }
    return {"version": 1, "modules": modules}


def normalize_upload_controls(value: Any) -> dict[str, Any]:
    normalized = default_upload_controls()
    if not isinstance(value, Mapping):
        return normalized
    source_modules = value.get("modules")
    if not isinstance(source_modules, Mapping):
        return normalized
    for module_key, target_module in normalized["modules"].items():
        source_module = source_modules.get(module_key)
        if not isinstance(source_module, Mapping):
            continue
        if "enabled" in source_module:
            target_module["enabled"] = bool(source_module["enabled"])
        source_fields = source_module.get("fields")
        if not isinstance(source_fields, Mapping):
            continue
        for field_key in target_module["fields"]:
            mode = source_fields.get(field_key)
            if mode in FIELD_MODES:
                target_module["fields"][field_key] = mode
        source_values = source_module.get("values")
        if isinstance(source_values, Mapping):
            for field_key in target_module["values"]:
                value = source_values.get(field_key)
                if isinstance(value, bool):
                    target_module["values"][field_key] = value
    return normalized


def module_enabled(controls: Any, module_key: str) -> bool:
    normalized = normalize_upload_controls(controls)
    return bool(normalized["modules"].get(module_key, {}).get("enabled", False))


def field_mode(controls: Any, module_key: str, field_key: str) -> str:
    normalized = normalize_upload_controls(controls)
    return normalized["modules"].get(module_key, {}).get("fields", {}).get(field_key, MODE_SKIP)


def runtime_switch_value(controls: Any, module_key: str, field_key: str) -> bool:
    normalized = normalize_upload_controls(controls)
    return bool(
        normalized["modules"].get(module_key, {}).get("values", {}).get(field_key, False)
    )


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:
            return ""
    except Exception:
        pass
    return str(value).strip()


def _normalize_column(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _safe_text(value).lower())


def _row_keys(row: Any) -> list[Any]:
    if hasattr(row, "index"):
        return list(row.index)
    if hasattr(row, "keys"):
        return list(row.keys())
    return []


def _row_value(row: Any, columns: tuple[str, ...]) -> str:
    keys = _row_keys(row)
    lookup = {_normalize_column(key): key for key in keys if _normalize_column(key)}
    for column in columns:
        value = _safe_text(row.get(column, ""))
        if value:
            return value
        matched = lookup.get(_normalize_column(column))
        if matched is not None:
            value = _safe_text(row.get(matched, ""))
            if value:
                return value
    return ""


def _copy_row(row: Any) -> Any:
    if hasattr(row, "copy"):
        return row.copy()
    return copy.copy(row)


def _blank_columns(row: Any, columns: tuple[str, ...]) -> None:
    normalized_targets = {_normalize_column(column) for column in columns}
    for key in _row_keys(row):
        if _normalize_column(key) in normalized_targets:
            row[key] = ""


def filter_row_for_module(row: Any, controls: Any, module_key: str) -> Any:
    filtered = _copy_row(row)
    normalized = normalize_upload_controls(controls)
    module = UPLOAD_CONTROL_SCHEMA.get(module_key)
    module_settings = normalized["modules"].get(module_key)
    if not module or not module_settings:
        return filtered
    enabled = bool(module_settings["enabled"])
    for field_key, definition in module["fields"].items():
        mode = module_settings["fields"][field_key]
        if not enabled or mode == MODE_SKIP:
            _blank_columns(filtered, tuple(definition.get("columns") or ()))
    return filtered


def _has_complete_price_tier(row: Any) -> bool:
    for index in range(1, 9):
        if _row_value(row, (f"Q{index}",)) and _row_value(row, (f"P{index}",)):
            return True
    return False


def validate_required_fields(row: Any, controls: Any) -> list[str]:
    normalized = normalize_upload_controls(controls)
    issues = []
    for module_key, module in UPLOAD_CONTROL_SCHEMA.items():
        module_settings = normalized["modules"][module_key]
        if not module_settings["enabled"]:
            continue
        for field_key, definition in module["fields"].items():
            if module_settings["fields"][field_key] != MODE_REQUIRED:
                continue
            if definition.get("runtime"):
                continue
            if definition.get("validator") == "price_tiers":
                present = _has_complete_price_tier(row)
            else:
                present = bool(_row_value(row, tuple(definition.get("columns") or ())))
            if not present:
                issues.append(f"字段控制要求填写：{module['label']} / {definition['label']}")
    return issues


def module_should_run(row: Any, controls: Any, module_key: str) -> bool:
    normalized = normalize_upload_controls(controls)
    module_settings = normalized["modules"].get(module_key)
    if not module_settings or not module_settings["enabled"]:
        return False
    return any(mode != MODE_SKIP for mode in module_settings["fields"].values())
