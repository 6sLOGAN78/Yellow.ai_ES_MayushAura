"""What "good" looks like, mined from the deployment's own conversations with
fixed, explicit rules (review §3.5, §4.2) — and the frozen golden set.

Eligibility for an exemplar session:
  1. inside the finding's baseline period (pre-change window, or the peer
     cohort in the same window for a cohort born broken);
  2. measured resolution and no unplanned handoff;
  3. no tool error, no empty tool answer and no KB miss *where that telemetry
     exists for the session's agent_kind*;
  4. the standard is the top decile: lowest turns for turns_to_resolve, the
     90th percentile of daily rates for resolution_rate.

The golden-set IDs are persisted to ``out/golden_set.json`` before any
prescription is written and are reused verbatim on later runs of the same
corpus; they are never recomputed after a fix has been proposed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Dict, List, Optional

from .coverage import Coverage
from .features import Corpus, median, percentile, rate, resolved, unplanned_handoff
from .io import write_json_atomic

GOLDEN_VERSION = "gs_v1"
GOLDEN_N = 30


def eligible(s: dict, cov: Coverage) -> bool:
    if not resolved(s) or unplanned_handoff(s):
        return False
    d = s["derived"]
    if cov.covered("tool_call", s) and (d["n_tool_errors"] or d["n_empty_success"]):
        return False
    if cov.covered("kb_lookup", s) and d["n_kb_lookups"] and d["n_kb_hits"] < d["n_kb_lookups"]:
        return False
    return True


def _daily_rates(rows: List[dict]) -> List[float]:
    by_day: Dict[int, List[int]] = {}
    for s in rows:
        by_day.setdefault(int(s["day"]), []).append(1 if resolved(s) else 0)
    return [sum(v) / float(len(v)) for d, v in sorted(by_day.items()) if len(v) >= 10]


def standards_for(obs: dict, cov: Coverage) -> List[dict]:
    base = obs["baseline_rows"]
    out = []
    if obs["metric"] == "resolution_rate":
        daily = _daily_rates(base)
        if len(daily) >= 3:
            best, med = percentile(daily, 0.90), median(daily)
            out.append({
                "tenant": obs["tenant"], "cohort": dict(obs["cohort"]), "metric": "resolution_rate",
                "exemplar_n": len(base), "golden_set_version": GOLDEN_VERSION,
                "best": round(best, 4), "median": round(med, 4),
                "deficit": round(best - (obs["observed"] or 0.0), 4),
                "derivation": ("daily resolution_rate over the baseline sessions (%s; %d sessions on %d days "
                               "with >= 10 sessions): best = 90th percentile day, median = median day, deficit = "
                               "best - observed %.4f in the window" % (obs["baseline_note"], len(base), len(daily),
                                                                     obs["observed"] or 0.0)),
            })
    ex = [s for s in base if eligible(s, cov)]
    turns = [float(s["turns"]) for s in ex]
    if len(turns) >= 20:
        best, med = percentile(turns, 0.10), median(turns)
        observed_turns = median([float(s["turns"]) for s in obs["rows_in"] if resolved(s)])
        out.append({
            "tenant": obs["tenant"], "cohort": dict(obs["cohort"]), "metric": "turns_to_resolve",
            "exemplar_n": len(ex), "golden_set_version": GOLDEN_VERSION,
            "best": float(best), "median": float(med),
            "deficit": round((observed_turns or med) - best, 2),
            "derivation": ("resolved sessions in the baseline (%s) with no unplanned handoff, no tool error, "
                           "no empty tool answer and no KB miss where that telemetry exists (%d of %d eligible): "
                           "best = lowest-decile turns, median = median turns; deficit = observed median turns "
                           "in the window (%s) - best" % (obs["baseline_note"], len(ex), len(base),
                                                          "%.1f" % observed_turns if observed_turns else "n/a")),
        })
    return out


def golden_candidates(obs: dict, cov: Coverage, n: int = GOLDEN_N) -> List[str]:
    """Deterministic: eligible baseline sessions, most efficient first, ties by id."""
    ex = [s for s in obs["baseline_rows"] if eligible(s, cov)]
    ex.sort(key=lambda s: (float(s["turns"]), s["session_id"]))
    return [s["session_id"] for s in ex[:n]]


def freeze_golden_sets(observations: List[dict], cov: Coverage, path: str, corpus_variant: str) -> dict:
    """Load the frozen golden sets for this corpus if present; otherwise select
    them now and persist before any prescription exists."""
    fingerprint = "%d/%s/%d" % (len(cov.corpus.sessions), ",".join(cov.corpus.tenants), cov.corpus.days)
    existing = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                existing = json.load(fh)
        except (OSError, ValueError):
            existing = None
        if existing and (existing.get("corpus") != corpus_variant
                         or existing.get("fingerprint") != fingerprint):
            existing = None
    sets = (existing or {}).get("sets") or {}
    changed = False
    for obs in observations:
        key = _key(obs)
        if key in sets:
            obs["golden_set"] = sets[key]["session_ids"]
            obs["golden_frozen_at"] = sets[key]["frozen_at"]
            continue
        ids = golden_candidates(obs, cov)
        sets[key] = {
            "tenant": obs["tenant"], "cohort": obs["cohort"], "window": [obs["a"], obs["b"] - 1],
            "session_ids": ids, "n": len(ids), "version": GOLDEN_VERSION,
            "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "rule": "eligible baseline sessions (resolved, no unplanned handoff, no tool error / empty answer / "
                    "KB miss where telemetry exists), lowest turns first, ties by session_id; none from the fault window",
        }
        obs["golden_set"] = ids
        obs["golden_frozen_at"] = sets[key]["frozen_at"]
        changed = True
    doc = {"corpus": corpus_variant, "fingerprint": fingerprint, "version": GOLDEN_VERSION, "sets": sets}
    if changed or not existing:
        write_json_atomic(path, doc, sort_keys=True)
    return doc


def _key(obs: dict) -> str:
    return "%s|%s" % (obs["tenant"], "|".join("%s=%s" % kv for kv in sorted(obs["cohort"].items())))
