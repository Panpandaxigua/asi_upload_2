# -*- coding: utf-8 -*-
"""Persisted software-controlled checkbox values for each ASI product."""
from __future__ import annotations

from collections.abc import Mapping


PRODUCT_FLAG_SCHEMA = {
    "distributor_only_view": {
        "label": "Distributor Only View（仅分销商可见）",
        "description": "勾选后，产品不会显示在终端买家网站。",
        "default": False,
    },
    "new_product": {
        "label": "New",
        "description": "控制 ASI 产品的 New 标记。",
        "default": True,
    },
    "seo_product": {
        "label": "SEO Product",
        "description": "控制 ASI 产品的 SEO Product 标记。",
        "default": False,
    },
    "close_out": {
        "label": "Close Out",
        "description": "控制 ASI 产品的 Close Out 标记。",
        "default": False,
    },
    "product_confirmed": {
        "label": "Product Confirmed through",
        "description": "只控制勾选状态，日期沿用 ASI 自动带出的值。",
        "default": True,
    },
    "prop65_no_chemicals": {
        "label": "Prop 65 无化学品声明",
        "description": "勾选 ASI 的无 Prop 65 化学品声明。",
        "default": True,
    },
    "unimprinted": {
        "label": "Unimprinted 默认选项",
        "description": "在 Imprint 页面勾选 Unimprinted。",
        "default": True,
    },
}


def default_product_flags() -> dict[str, bool]:
    return {
        key: bool(definition["default"])
        for key, definition in PRODUCT_FLAG_SCHEMA.items()
    }


def normalize_product_flags(value) -> dict[str, bool]:
    normalized = default_product_flags()
    if not isinstance(value, Mapping):
        return normalized
    for key in normalized:
        stored = value.get(key)
        if isinstance(stored, bool):
            normalized[key] = stored
    return normalized
