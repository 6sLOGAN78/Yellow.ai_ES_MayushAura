"""The metric constructor — the "number passport" (review §1.3).

Every metric the system authors goes through ``metric()`` so fidelity, coverage,
plan and (for judged values) calibration are attached at calculation time and
cannot be forgotten later.
"""
from __future__ import annotations

from typing import Dict, List, Optional


FIDELITIES = ("measured", "judged", "derived")


def metric(id: str, name: str, ask_id: str, grain: str, fidelity: str, value,
           coverage_value: float, coverage_basis: str, source: str, filter_: str,
           denominator: str, breakdowns: Optional[List[str]] = None,
           excluded: Optional[List[str]] = None, alternatives: Optional[List[str]] = None,
           calibration: Optional[Dict] = None, value_note: Optional[str] = None,
           breakdown: Optional[Dict] = None, coverage_extra: Optional[Dict] = None) -> dict:
    if fidelity not in FIDELITIES:
        raise ValueError("fidelity must be one of %s" % (FIDELITIES,))
    if fidelity == "judged" and not calibration:
        raise ValueError("a judged metric must publish a calibration or be filed as a gap")
    if calibration and calibration.get("agreement", 0) >= 0.995:
        raise ValueError("calibration of %.3f on %s is a bug, not a judge — the label set has "
                         "human disagreement baked in" % (calibration["agreement"], id))
    m = {
        "id": id,
        "name": name,
        "ask_id": ask_id,
        "grain": grain,
        "fidelity": fidelity,
        "value": value,
        "coverage": {
            "value": round(float(coverage_value), 4),
            "basis": coverage_basis,
        },
        "calibration": calibration,
        "plan": {
            "source": source,
            "filter": filter_,
            "denominator": denominator,
            "breakdowns": list(breakdowns or []),
        },
    }
    if excluded:
        m["coverage"]["excluded"] = list(excluded)
    if coverage_extra:
        m["coverage"].update(coverage_extra)
    if alternatives:
        m["plan"]["alternatives_offered"] = list(alternatives)
    if value_note:
        m["value_note"] = value_note
    if breakdown:
        m["breakdown"] = breakdown
    return m


def gap(ask_id: str, verdict: str, why: str, nearest_proxy: Optional[str] = None,
        why_the_proxy_misleads: Optional[str] = None, required_event: Optional[Dict] = None) -> dict:
    g = {"ask_id": ask_id, "verdict": verdict, "why": why}
    if nearest_proxy:
        g["nearest_proxy"] = nearest_proxy
    if why_the_proxy_misleads:
        g["why_the_proxy_misleads"] = why_the_proxy_misleads
    if required_event:
        g["required_event"] = required_event
    return g


def r4(x):
    return None if x is None else round(float(x), 4)
