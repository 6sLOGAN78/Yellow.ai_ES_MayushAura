"""Schema validation of the report.

Uses ``jsonschema`` when it is importable; otherwise a small built-in checker
covering the subset of draft-07 the loop-report schema uses (type, required,
properties, items, enum, minimum/maximum, minItems, if/then, nullable type
lists). Both return a list of human-readable problems; empty means valid.
"""
from __future__ import annotations

import json
from typing import Any, List


def load_schema(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate(report: dict, schema: dict) -> List[str]:
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return _check(report, schema, "$")
    v = jsonschema.Draft7Validator(schema)
    return ["%s: %s" % ("$" + "".join("[%r]" % p for p in e.absolute_path), e.message)
            for e in sorted(v.iter_errors(report), key=lambda e: list(e.absolute_path))]


_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
}


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
            errs.append("%s: expected type %s, got %s" % (path, "/".join(types), type(value).__name__))
            return errs
    if "enum" in schema and value not in schema["enum"]:
        errs.append("%s: %r is not one of %s" % (path, value, schema["enum"]))
    if "const" in schema and value != schema["const"]:
        errs.append("%s: %r != const %r" % (path, value, schema["const"]))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append("%s: %r < minimum %r" % (path, value, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            errs.append("%s: %r > maximum %r" % (path, value, schema["maximum"]))
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append("%s: missing required property %r" % (path, k))
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
            errs.append("%s: %d items < minItems %d" % (path, len(value), schema["minItems"]))
        if "items" in schema:
            for i, item in enumerate(value):
                errs.extend(_check(item, schema["items"], "%s[%d]" % (path, i)))
    return errs


def extra_checks(report: dict) -> List[str]:
    """Cross-field rules the schema cannot express but the scorer relies on."""
    errs = []
    fids = {f["id"] for f in report.get("findings", [])}
    dids = {d["id"] for d in report.get("diagnoses", [])}
    pids = {p["id"] for p in report.get("prescriptions", [])}
    for d in report.get("diagnoses", []):
        if d.get("finding_id") not in fids:
            errs.append("diagnosis %s references unknown finding %s" % (d["id"], d.get("finding_id")))
    for p in report.get("prescriptions", []):
        if p.get("diagnosis_id") not in dids:
            errs.append("prescription %s references unknown diagnosis %s" % (p["id"], p.get("diagnosis_id")))
    for v in report.get("verifications", []):
        if v.get("prescription_id") not in pids:
            errs.append("verification references unknown prescription %s" % v.get("prescription_id"))
    for f in report.get("findings", []):
        if not f.get("is_regression") and not f.get("not_a_regression_because"):
            errs.append("finding %s is not a regression but gives no not_a_regression_because" % f["id"])
    for m in report.get("metrics", []):
        cal = m.get("calibration")
        if cal and cal.get("agreement", 0) >= 0.995:
            errs.append("metric %s publishes a calibration >= 0.995" % m["id"])
        if m.get("fidelity") == "judged" and not cal:
            errs.append("metric %s is judged but carries no calibration" % m["id"])
    return errs
