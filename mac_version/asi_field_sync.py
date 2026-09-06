# -*- coding: utf-8 -*-
"""Versioned cache for fields discovered directly from the ASI application."""

import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


REGISTRY_SCHEMA_VERSION = 1


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_field(field):
    if not isinstance(field, dict):
        return None
    normalized = {
        "key": str(field.get("key") or "").strip(),
        "module": str(field.get("module") or "other").strip(),
        "label": str(field.get("label") or "").strip(),
        "type": str(field.get("type") or "text").strip().lower(),
    }
    for name in ("id", "name", "binding", "data_bind", "source"):
        value = str(field.get(name) or "").strip()
        if value:
            normalized[name] = value
    if "required" in field:
        normalized["required"] = bool(field["required"])
    options = field.get("options")
    if isinstance(options, list):
        normalized["options"] = [str(option).strip() for option in options if str(option).strip()]
    if not normalized["key"]:
        identity = normalized.get("id") or normalized.get("name") or normalized.get("binding") or normalized["label"]
        normalized["key"] = f"{normalized['module']}:{identity}" if identity else ""
    if not normalized["key"] or not (normalized["label"] or normalized.get("binding") or normalized.get("id")):
        return None
    return normalized


def normalize_field_registry(capture):
    if not isinstance(capture, dict):
        raise ValueError("ASI 字段抓取结果不是有效对象。")
    fields_by_key = {}
    for raw in capture.get("fields") or []:
        field = _normalize_field(raw)
        if field:
            fields_by_key[field["key"]] = field
    if not fields_by_key:
        raise ValueError("ASI 页面没有抓取到任何字段；为保护现有配置，本次不会覆盖缓存。")
    fields = [fields_by_key[key] for key in sorted(fields_by_key)]
    result = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_url": str(capture.get("source_url") or ""),
        "source_scripts": sorted({str(item) for item in capture.get("source_scripts") or [] if item}),
        "scan_mode": str(capture.get("scan_mode") or ""),
        "scanned_routes": [str(item) for item in capture.get("scanned_routes") or [] if item],
        "field_count": len(fields),
        "fields": fields,
    }
    result["fingerprint"] = hashlib.sha256(_canonical(fields).encode("utf-8")).hexdigest()
    return result


def _load_existing(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def install_field_registry(capture, target_path):
    """Validate, diff, and atomically install a live ASI field capture."""
    target = Path(target_path)
    current = normalize_field_registry(capture)
    previous = _load_existing(target)
    old_fields = {item.get("key"): item for item in previous.get("fields") or [] if item.get("key")}
    new_fields = {item["key"]: item for item in current["fields"]}
    old_keys, new_keys = set(old_fields), set(new_fields)
    changed = sum(old_fields[key] != new_fields[key] for key in old_keys & new_keys)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return {
        "path": str(target),
        "total": len(new_fields),
        "added": len(new_keys - old_keys),
        "removed": len(old_keys - new_keys),
        "changed": changed,
        "fingerprint": current["fingerprint"],
    }


def install_live_options(catalog, bundled_path, target_path):
    """Merge live ASI lookup values into the full bundled option/rule document."""
    if not isinstance(catalog, dict):
        raise ValueError("ASI 候选值抓取结果不是有效对象。")
    bundled = json.loads(Path(bundled_path).read_text(encoding="utf-8-sig"))
    merged = deepcopy(bundled)
    updated_groups = 0
    updated_values = 0
    group_counts = {}
    for key, raw_values in catalog.items():
        if key not in merged or key == "validation_rules" or not isinstance(raw_values, list) or not raw_values:
            continue
        values = []
        seen = set()
        existing_values = merged[key].get("options") or []
        existing_markers = {
            (_canonical(item).casefold() if isinstance(item, str) else _canonical(item))
            for item in existing_values
        }
        for raw in [*existing_values, *raw_values]:
            value = raw if isinstance(raw, dict) else str(raw).strip()
            marker = _canonical(value).casefold() if isinstance(value, str) else _canonical(value)
            if not value or marker in seen:
                continue
            seen.add(marker)
            values.append(value)
        if not values:
            continue
        merged[key]["options"] = values
        updated_groups += 1
        updated_values += sum(
            1 for item in values
            if (_canonical(item).casefold() if isinstance(item, str) else _canonical(item)) not in existing_markers
        )
        group_counts[key] = len(values)
    if not updated_groups:
        raise ValueError("ASI 没有返回可用于更新的候选值；现有配置未被覆盖。")

    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return {
        "options_path": str(target),
        "updated_groups": updated_groups,
        "updated_values": updated_values,
        "option_group_counts": group_counts,
    }
