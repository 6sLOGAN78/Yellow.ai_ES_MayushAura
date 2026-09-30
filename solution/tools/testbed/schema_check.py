"""Minimal draft-07 checker for loop-report.json.

Independent of any candidate's validator. Uses ``jsonschema`` when importable,
otherwise a small built-in covering the subset the schema uses: type lists,
required, properties, items, enum, const, minimum/maximum, minItems, if/then/else.

``problems(report, schema_path)`` returns a list of human-readable strings;
empty means valid.
"""
from __future__ import annotations

import json
from typing import Any, List

try:  # full draft-07 validation when available; otherwise a built-in subset
    import jsonschema as _jsonschema  # type: ignore
    BACKEND = "jsonschema"
except ImportError:  # pragma: no cover
    _jsonschema = None
    BACKEND = "builtin"

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
}


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def problems(report: dict, schema: dict) -> List[str]:
    if _jsonschema is None:
        return _check(report, schema, "$")
    v = _jsonschema.Draft7Validator(schema)
    return [
        "%s: %s" % ("$" + "".join("[%r]" % p for p in e.absolute_path), e.message)
        for e in sorted(v.iter_errors(report), key=lambda e: list(e.absolute_path))
    ]


def _is_type(value: Any, t: str) -> bool:
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (
            isinstance(value, float) and value.is_integer())
    return isinstance(value, _TYPES[t])


def _check(value: Any, schema: dict, path: str) -> List[str]:
    errs: List[str] = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(value, x) for x in types):
            return ["%s: expected type %s, got %s"
                    % (path, "/".join(types), type(value).__name__)]
    if "enum" in schema and value not in schema["enum"]:
        errs.append("%s: %r not in enum" % (path, value))
    if "const" in schema and value != schema["const"]:
        errs.append("%s: %r != const %r" % (path, value, schema["const"]))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append("%s: %r < %r" % (path, value, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            errs.append("%s: %r > %r" % (path, value, schema["maximum"]))
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append("%s: missing required %r" % (path, k))
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                errs.extend(_check(value[k], sub, "%s.%s" % (path, k)))
        if "if" in schema:
            if not _check(value, schema["if"], path):
                if "then" in schema:
                    errs.extend(_check(value, schema["then"], path))
            elif "else" in schema:
                errs.extend(_check(value, schema["else"], path))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append("%s: %d < minItems %d" % (path, len(value), schema["minItems"]))
        if "items" in schema:
            for i, item in enumerate(value):
                errs.extend(_check(item, schema["items"], "%s[%d]" % (path, i)))
    return errs
