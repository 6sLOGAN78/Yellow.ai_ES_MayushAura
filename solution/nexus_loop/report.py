"""Assemble a schema-shaped ``loop-report.json``.

Internal detector rows (session lists, golden IDs) never enter the report.
Every number that does has already been computed by the metric helper, the
impact block, or the replay client.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Dict, List, Optional, Tuple

from .coverage import Coverage
from .diagnose import diagnosis, prescription
from .features import Corpus
from .impact import AUDIENCE, impact_for, if_nothing_changes, severity_for
from .metrics import r4
from .standards import standards_for


CAUSE_HEADLINE = {
    "kb.gap": "Knowledge gap — retrieval never grounds the answer",
    "tool.contract_break": "Silent tool failure — HTTP 200 with nothing in the body",
    "prompt.regression": "Prompt regression — same outcomes, more turns and cost",
    "model.change": "Model change moved effort, not outcomes",
    "unknown": "Measured regression, cause not attributed",
    "traffic_mix": "Traffic mix shift, not a quality change",
    "load": "Load spike, not a quality regression",
    "judge_change": "Judge-version boundary, not an agent regression",
}


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, Counter):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, float):
        return round(x, 6) if x == x and x not in (float("inf"), float("-inf")) else None
    return x


def _slug(obs: dict, n: int) -> str:
    t = (obs.get("tenant") or "all").replace("*", "all").split("-")[0]
    return "f%02d_%s_%s" % (n, obs["kind"], t)


def _window(obs: dict) -> dict:
    return {"from_day": int(obs["a"]), "to_day": int(obs["b"]) - 1}


def finding_from(fid: str, obs: dict, corpus: Corpus, cov: Coverage,
                 is_regression: bool) -> dict:
    f = {
        "id": fid,
        "tenant": obs["tenant"],
        "cohort": dict(obs.get("cohort") or {}),
        "metric": obs["metric"],
        "window": _window(obs),
        "is_regression": is_regression,
    }
    if obs.get("observed") is not None:
        f["observed"] = r4(obs["observed"]) if isinstance(obs["observed"], (int, float)) \
            else obs["observed"]
    if obs.get("expected") is not None:
        f["expected"] = r4(obs["expected"]) if isinstance(obs["expected"], (int, float)) \
            else obs["expected"]
    if obs.get("evidence"):
        f["evidence"] = list(obs["evidence"])
    if is_regression:
        imp = impact_for(obs, corpus, cov)
        f["severity"] = severity_for(obs, imp)
        f["impact"] = imp
        f["audience"] = list(AUDIENCE.get(obs["cause_class"], ["agent_builder", "platform_owner"]))
        f["if_nothing_changes"] = if_nothing_changes(obs, imp)
        f["headline"] = CAUSE_HEADLINE.get(obs["cause_class"], "Regression")
        f["baseline"] = obs.get("baseline_note")
    else:
        f["not_a_regression_because"] = obs["not_because"]
        f["severity"] = "low"
        f["headline"] = CAUSE_HEADLINE.get(obs["cause_class"], "Not a regression")
    return f


def replay_priority(obs: dict) -> int:
    """tool.validate first (strongest kit signal), then kb.add, then prompt.edit."""
    return {"tool.contract_break": 0, "kb.gap": 1, "prompt.regression": 2,
            "model.change": 3}.get(obs.get("cause_class"), 9)


def assemble(team: str, corpus: Corpus, cov: Coverage, metrics: List[dict],
             gaps: List[dict], regressions: List[dict], decoys: List[dict],
             verifications: Optional[List[dict]] = None,
             system_notes: str = "", extras: Optional[dict] = None,
             prior_approvals: Optional[Dict[tuple, dict]] = None) -> dict:
    """Build the report dict. Golden sets must already be frozen onto each
    regression observation (``obs['golden_set']``) before this is called.

    ``prior_approvals`` maps (prescription id, diagnosis id, target) -> approval
    from an earlier report; a matching prescription keeps its human verdict so a
    re-run does not silently wipe the decision record."""
    verifications = list(verifications or [])
    prior_approvals = prior_approvals or {}
    findings: List[dict] = []
    diagnoses: List[dict] = []
    prescriptions: List[dict] = []
    standard: List[dict] = []
    presc_tenants: Dict[str, str] = {}
    n = 0

    ranked = sorted(regressions, key=lambda o: (replay_priority(o), -float(o.get("confidence") or 0)))
    for obs in ranked + decoys:
        n += 1
        fid = _slug(obs, n)
        did = "d" + fid[1:]
        is_reg = obs in ranked
        findings.append(finding_from(fid, obs, corpus, cov, is_reg))
        diagnoses.append(diagnosis(did, fid, obs))
        if is_reg:
            stds = standards_for(obs, cov)
            standard.extend(stds)
            p = prescription("p" + fid[1:], did, obs, stds[0] if stds else None)
            if p:
                p["golden_set"] = list(obs.get("golden_set") or [])
                p["tenant"] = obs["tenant"]
                prior = prior_approvals.get((p["id"], p.get("diagnosis_id"), p.get("target")))
                if prior:
                    p["approval"] = prior
                prescriptions.append(p)
                presc_tenants[p["id"]] = obs["tenant"]

    sa = self_assessment(prescriptions, verifications)
    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = {
        "team": team,
        "corpus": corpus.kit.corpus_variant,
        "generated_at": generated,
        "system_notes": system_notes,
        "metrics": [_jsonable(m) for m in metrics],
        "standard": standard,
        "findings": findings,
        "diagnoses": diagnoses,
        "prescriptions": prescriptions,
        "verifications": verifications,
        "gaps": gaps,
        "self_assessment": sa,
    }
    report["_presc_tenants"] = presc_tenants  # stripped before write
    return report


def strip_internal(report: dict) -> dict:
    """Drop keys the schema-facing file should not carry."""
    out = {k: v for k, v in report.items() if not k.startswith("_")}
    for p in out.get("prescriptions") or []:
        # keep golden_set for the replay client / UI count; it is extra, allowed
        p.pop("tenant", None)
    return out


def self_assessment(prescriptions: List[dict], verifications: List[dict]) -> dict:
    by_pid = {p["id"]: p for p in prescriptions}
    by_type: Dict[str, List[dict]] = {}
    for v in verifications:
        p = by_pid.get(v.get("prescription_id"))
        if not p:
            continue
        by_type.setdefault(p["change_type"], []).append(v)
    accuracy = {}
    downweighted = []
    for ct, vs in sorted(by_type.items()):
        errors = [v["prediction_error"] for v in vs if v.get("prediction_error") is not None]
        hits = [v for v in vs if v.get("verdict") == "improved"]
        accuracy[ct] = {
            "n": len(vs),
            "hit_rate": round(len(hits) / float(len(vs)), 4) if vs else 0.0,
            "mean_prediction_error": round(sum(errors) / float(len(errors)), 4) if errors else None,
        }
        if vs and not hits:
            downweighted.append(ct)
    cycles = len(verifications)
    if cycles == 0:
        notes = ("No replay cycle this run: the endpoint was unreachable or --no-replay was set. "
                 "Priors are empty and nothing is de-weighted.")
    elif cycles < 3:
        notes = ("Only %d replay cycle(s), so these priors carry no weight yet. With n<3 per "
                 "change class we report the numbers and decline to act on them — a hit rate "
                 "from a single observation is not evidence. Depth was spent on one high-"
                 "confidence tool.validate rather than burning budget on probes." % cycles)
    else:
        notes = ("%d genuine replay cycles recorded (no null/wrong calibration probes). "
                 "Change types with no_effect are listed in downweighted." % cycles)
    sa = {"cycles": cycles, "prescription_accuracy": accuracy,
          "downweighted": downweighted, "notes": notes}
    return sa


def attach_verifications(report: dict, vers: List[dict]) -> None:
    report["verifications"] = vers
    report["self_assessment"] = self_assessment(report.get("prescriptions") or [], vers)


def replay_queue(report: dict) -> List[Tuple[dict, str]]:
    """Prescriptions in replay priority, with tenant."""
    tenants = report.get("_presc_tenants") or {}
    diags = {d["id"]: d for d in report.get("diagnoses") or []}
    findings = {f["id"]: f for f in report.get("findings") or []}
    ranked = []
    for p in report.get("prescriptions") or []:
        d = diags.get(p["diagnosis_id"]) or {}
        f = findings.get(d.get("finding_id")) or {}
        cause = d.get("cause_class")
        order = {"tool.contract_break": 0, "kb.gap": 1, "prompt.regression": 2}.get(cause, 9)
        tenant = tenants.get(p["id"]) or f.get("tenant")
        ranked.append((order, p, tenant))
    ranked.sort(key=lambda x: x[0])
    return [(p, tenant) for _, p, tenant in ranked if tenant]
