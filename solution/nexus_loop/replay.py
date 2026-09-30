"""Replay client — verification of a hypothesis we already hold.

Policy (review §4): diagnose offline first; send one run per high-confidence
prescription on the exact detected cohort and window with the frozen golden
set; record whatever comes back. ``no_effect`` is a result, not an error. No
calibration probes, no null fixes, no retries on ``golden_set.pass = false``.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Dict, List, Optional


class ReplayUnavailable(RuntimeError):
    pass


def health(url: str, timeout: float = 3.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def body_for(team: str, tenant: str, presc: dict) -> dict:
    cohort = {k: v for k, v in (presc.get("cohort") or {}).items()}
    return {
        "team": team,
        "tenant": tenant,
        "change": {"type": presc["change_type"], "target": presc["target"],
                   "description": presc["description"]},
        "cohort": cohort,
        "golden_set": list(presc.get("golden_set") or []),
    }


def post_replay(url: str, body: dict, timeout: float = 20.0) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + "/replay", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise ReplayUnavailable("replay endpoint returned HTTP %d: %s" % (e.code, detail[:300]))
    except (urllib.error.URLError, OSError) as e:
        raise ReplayUnavailable("replay endpoint unreachable: %s" % e)
    except ValueError as e:
        # a 200 with a non-JSON or non-UTF-8 body raises JSONDecodeError or
        # UnicodeDecodeError, both ValueError. Letting one escape would crash
        # run.py before the report is written, so treat it as unavailable.
        raise ReplayUnavailable("replay endpoint returned an unreadable body: %s" % e)


def verification_from(presc: dict, resp: dict) -> dict:
    pd = presc["predicted_delta"]
    before, after = resp.get("before"), resp.get("after")
    predicted = float(pd["to"]) - float(pd["from"])
    observed = (float(after) - float(before)) if before is not None and after is not None else None
    v = {
        "prescription_id": presc["id"],
        "replay_run_id": str(resp.get("run_id", "")),
        "metric": resp.get("metric") or pd["metric"],
        "before": before,
        "after": after,
        "golden_set_pass": (resp.get("golden_set") or {}).get("pass"),
        "verdict": resp.get("verdict", "no_effect"),
        "prediction_error": round(predicted - observed, 4) if observed is not None else None,
        "golden_set_replayed": (resp.get("golden_set") or {}).get("replayed"),
        "golden_set_regressed": (resp.get("golden_set") or {}).get("regressed"),
        "sessions_replayed": resp.get("sessions_replayed"),
        "runs_used": resp.get("runs_used"),
        "run_budget": resp.get("run_budget"),
        "note": ("replay 'before' is the endpoint's own simulated baseline, not this system's corpus "
                 "measurement; prediction_error compares the predicted delta with the replayed delta"),
    }
    if v["prediction_error"] is None:
        del v["prediction_error"]
    return v


def run_all(url: str, team: str, prescriptions: List[dict], tenants: Dict[str, str],
            max_runs: int, audit_path: Optional[str]) -> (List[dict], List[str]):
    """Replay up to ``max_runs`` prescriptions, in the given priority order."""
    notes: List[str] = []
    vers: List[dict] = []
    if not url:
        notes.append("replay: no --replay-url given; verifications skipped, report still valid")
        return vers, notes
    h = health(url)
    if not h:
        notes.append("replay: endpoint %s unreachable at run time; verifications skipped" % url)
        return vers, notes
    for presc in prescriptions[:max_runs]:
        body = body_for(team, tenants[presc["id"]], presc)
        try:
            resp = post_replay(url, body)
        except ReplayUnavailable as e:
            notes.append("replay %s: %s" % (presc["id"], e))
            continue
        if "error" in resp:
            notes.append("replay %s: endpoint error %s" % (presc["id"], resp.get("error")))
            continue
        vers.append(verification_from(presc, resp))
        notes.append("replay %s: %s %s on %s -> %s (run %s, before %s after %s, golden %s)" % (
            presc["id"], presc["change_type"], presc["target"], json.dumps(body["cohort"], sort_keys=True),
            resp.get("verdict"), resp.get("run_id"), resp.get("before"), resp.get("after"),
            (resp.get("golden_set") or {}).get("pass")))
        if audit_path:
            os.makedirs(os.path.dirname(os.path.abspath(audit_path)), exist_ok=True)
            with open(audit_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"request": body, "response": resp}, sort_keys=True) + "\n")
    return vers, notes
