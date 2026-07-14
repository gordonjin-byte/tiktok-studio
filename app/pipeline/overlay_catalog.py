"""Reads the Remotion-side generated template catalog (remotion/src/templates/
catalog.json) — the language-neutral manifest the Node/Remotion project's
`npm run catalog:build` regenerates from its typed zod schemas. Python never
parses TSX/zod directly; this is the single read path both the advisor prompt
and the per-cue prop-editing UI go through."""
from __future__ import annotations

import json
import time
from typing import Any, Optional

import jsonschema

from .. import config

_cache: dict[str, Any] = {"mtime": None, "data": None}


class TemplateDef(dict):
    """A single catalog entry — plain dict subclass for convenient .get()
    access from callers that don't need strict typing."""


def load_catalog() -> dict:
    """Returns the raw catalog.json content: {"catalog_version": str,
    "templates": [...]}. Cached, invalidated on file mtime change so a
    `catalog:build` re-run during development is picked up without restart."""
    path = config.TEMPLATE_CATALOG_PATH
    if not path.exists():
        return {"catalog_version": "unbuilt", "templates": []}
    mtime = path.stat().st_mtime
    if _cache["data"] is None or _cache["mtime"] != mtime:
        _cache["data"] = json.loads(path.read_text())
        _cache["mtime"] = mtime
    return _cache["data"]


def catalog_version() -> str:
    return load_catalog().get("catalog_version", "unbuilt")


def all_templates() -> list[dict]:
    return load_catalog().get("templates", [])


def template(template_id: str) -> Optional[dict]:
    for t in all_templates():
        if t["id"] == template_id:
            return t
    return None


def templates_for(cue_type: str) -> list[dict]:
    return [t for t in all_templates() if cue_type in t.get("applicable_cue_types", [])]


def fallback_template_id() -> str:
    for t in all_templates():
        if t.get("is_fallback"):
            return t["id"]
    return "generic-caption-card"


def validate_props(template_id: str, props: dict) -> dict:
    """Validates props against the template's JSON Schema. Raises
    jsonschema.ValidationError on mismatch — callers (overlay_advisor's
    validation-retry loop) treat that like any other response-validation
    failure. Returns props unchanged on success (pass-through, for chaining)."""
    t = template(template_id)
    if t is None:
        raise jsonschema.ValidationError(f"unknown template_id: {template_id}")
    schema = t.get("props_schema") or {}
    jsonschema.validate(instance=props, schema=schema)
    return props


def props_schema_ui(template_id: str) -> list[dict[str, Any]]:
    """Flattens a template's JSON Schema into settings_schema()-shaped UI
    control descriptors ({key, label, type, default, options, min, max}),
    reused by the per-cue prop-editing panel the same way settings_schema()
    drives the global render-settings panel."""
    t = template(template_id)
    if t is None:
        return []
    schema = t.get("props_schema") or {}
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    out: list[dict[str, Any]] = []
    for key, spec in props.items():
        entry: dict[str, Any] = {
            "key": key, "label": key.replace("_", " ").title(),
            "required": key in required,
        }
        if "enum" in spec:
            entry["type"] = "enum"
            entry["options"] = spec["enum"]
        elif spec.get("type") == "boolean":
            entry["type"] = "bool"
        elif spec.get("type") in ("number", "integer"):
            entry["type"] = "number"
            if "minimum" in spec:
                entry["min"] = spec["minimum"]
            if "maximum" in spec:
                entry["max"] = spec["maximum"]
        elif spec.get("type") == "string" and spec.get("pattern", "").startswith("^#"):
            entry["type"] = "color"
        else:
            entry["type"] = "text"
            if "maxLength" in spec:
                entry["maxLength"] = spec["maxLength"]
        if "default" in spec:
            entry["default"] = spec["default"]
        out.append(entry)
    return out
