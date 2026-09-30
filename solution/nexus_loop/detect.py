"""Fault detectors. Each owns its own cohort dimensions (review §3.1):

* tool contract break   — (tenant, tool_name) → intent      on empty-success rate
* KB gap / resolution   — (tenant, intent)                   on resolution rate
* prompt/efficiency     — (tenant, agent_id)                 on turns to resolve

Observation and attribution are separate (review §3.2): a regression whose
mechanism is proven is reported even when no config change aligns, with
``cause_class: unknown`` and ``attributed_change: null``.

Every candidate comes from the data and the config timeline. No practice
tenant / day / intent / tool / agent literal appears in this module.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Optional

from .coverage import Coverage
from .features import (Corpus, abandoned, mean, median, percentile, rate, resolved,
                       unplanned_handoff)
from .io import format_change
from .series import DaySeries, best_window, elevated_run


class T:
    """Detection thresholds. Relative or statistical, never a practice value."""
    # tool contract break
    TOOL_MIN_CALLS_IN = 60
    TOOL_MIN_RATE = 0.05           # inside empty-success rate must be at least this
    TOOL_MIN_LIFT_ABS = 0.04
    TOOL_MIN_LIFT_REL = 3.0
    TOOL_MIN_Z = 4.0
    # resolution drop / KB gap
    RES_MIN_N_IN = 60
    RES_MIN_DROP = 0.05
    RES_MIN_Z = 4.0
    UNKNOWN_MIN_DROP = 0.10        # bar for a regression with no proven mechanism
    UNKNOWN_MIN_Z = 6.0
    KB_LOOKUP_SHARE = 0.30         # cohort counts as KB-backed above this
    KB_HIT_LOW = 0.30
    KB_HIT_PEER_OK = 0.60
    TOOL_INVOLVED_SHARE = 0.10
    # efficiency
    EFF_MIN_N_IN = 120
    EFF_MIN_RATIO = 1.20
    EFF_MIN_Z = 5.0
    EFF_PEER_TOLERANCE = 0.4       # peers may show at most this fraction of the rise
    OUTCOME_FLAT = 0.05
    LOOKBACK = 14
    MIN_DAYS_OUT = 7               # a window needs a week of contrast outside it
    NEW_COHORT_LOOKBACK = 5        # a cohort starting within this many days of the window is born broken
    ALIGN_BEFORE, ALIGN_AFTER = 3, 2


def _pre_rows(rows: List[dict], a: int, lookback: int = T.LOOKBACK) -> List[dict]:
    return [r for r in rows if max(0, a - lookback) <= int(r["day"]) < a]


def _in_rows(rows: List[dict], a: int, b: int) -> List[dict]:
    return [r for r in rows if a <= int(r["day"]) < b]


def _res_rate(rows: List[dict]) -> Optional[float]:
    return rate(sum(1 for r in rows if resolved(r)), len(rows))


def _fmt(x, nd=3):
    return "n/a" if x is None else ("%.*f" % (nd, x))


def _fmt_set(xs) -> str:
    return ",".join(sorted(str(x) for x in xs)) if xs else "n/a"


def _changed_dimension(rows_in: List[dict], rows_base: List[dict]):
    """Which of the agent's config dimensions actually changed between the
    baseline and the window, read from the sessions themselves. Attribution
    uses this instead of proximity, because an innocent change can share the
    onset day with the guilty one."""
    pv = ({r.get("prompt_version") for r in rows_base if r.get("prompt_version")},
          {r.get("prompt_version") for r in rows_in if r.get("prompt_version")})
    md = ({r.get("model") for r in rows_base if r.get("model")},
          {r.get("model") for r in rows_in if r.get("model")})
    prompt_changed = bool(pv[0] and pv[1] and pv[0] != pv[1])
    model_changed = bool(md[0] and md[1] and md[0] != md[1])
    return prompt_changed, model_changed, pv, md


# ------------------------------------------------------------------ tool ---

def detect_tool_contract(corpus: Corpus, cov: Coverage) -> List[dict]:
    """Silent tool failure: HTTP 200 / outcome ok / result_field_count 0."""
    D = corpus.days
    out: List[dict] = []
    for tenant in corpus.tenants:
        rows = [s for s in corpus.rows(tenant) if cov.covered("tool_call", s)]
        tools = Counter()
        for s in rows:
            tools.update(s["derived"]["calls_by_tool"])
        for tool, n_calls in tools.items():
            if n_calls < 2 * T.TOOL_MIN_CALLS_IN:
                continue
            empty = DaySeries(D)
            errs = DaySeries(D)
            for s in rows:
                d = s["derived"]
                c = d["calls_by_tool"].get(tool, 0)
                if not c:
                    continue
                e = d["errors_by_tool"].get(tool, 0)
                empty.add_rate(int(s["day"]), d["empty_by_tool"].get(tool, 0), c - e)
                errs.add_rate(int(s["day"]), e, c)
            w = best_window(empty, "up", min_len=3, max_len=D, min_n_in=T.TOOL_MIN_CALLS_IN,
                            min_n_out=T.TOOL_MIN_CALLS_IN, min_days_out=T.MIN_DAYS_OUT)
            if not w:
                continue
            lift_ok = (w.inside >= T.TOOL_MIN_RATE
                       and w.inside - w.outside >= T.TOOL_MIN_LIFT_ABS
                       and w.inside >= T.TOOL_MIN_LIFT_REL * max(w.outside, 0.005)
                       and w.z >= T.TOOL_MIN_Z)
            if not lift_ok:
                continue
            a = w.a
            b = max(w.b, elevated_run(empty, a, w.outside + (w.inside - w.outside) / 2.0, "up") + 1)

            users = [s for s in rows if s["derived"]["calls_by_tool"].get(tool)]
            in_rows = _in_rows(users, a, b)
            intents = Counter(s["intent"] for s in in_rows)
            top_intent, top_n = intents.most_common(1)[0]
            cohort: Dict[str, str] = {"tool": tool}
            if top_n / float(len(in_rows)) >= 0.5:
                cohort = {"intent": top_intent, "tool": tool}
                cohort_rows_all = [s for s in corpus.rows(tenant) if s["intent"] == top_intent]
            else:
                cohort_rows_all = users
            cohort_in = _in_rows(cohort_rows_all, a, b)
            pre = _pre_rows(cohort_rows_all, a)
            baseline_rows, baseline_note = pre, (
                "this cohort's own resolution over the %d days before the break" % T.LOOKBACK)
            if len(pre) < T.RES_MIN_N_IN:
                post = [s for s in cohort_rows_all if int(s["day"]) >= b]
                baseline_rows, baseline_note = post, "this cohort's own resolution after the break"
            observed, expected = _res_rate(cohort_in), _res_rate(baseline_rows)

            err_in = errs.mean_between(a, b) or 0.0
            err_out_n = errs.total() - sum(errs.n[a:b])
            err_out = ((sum(errs.s) - sum(errs.s[a:b])) / err_out_n) if err_out_n else 0.0
            retry_in = rate(sum(1 for s in in_rows if s["derived"]["n_same_tool_retry"]), len(in_rows))
            retry_pre = rate(sum(1 for s in _pre_rows(users, a) if s["derived"]["n_same_tool_retry"]),
                             len(_pre_rows(users, a)))
            change = corpus.nearest_change(tenant, a, {"tool"}, target=tool,
                                           before=T.ALIGN_BEFORE, after=T.ALIGN_AFTER)
            evidence = [
                "%s: %.1f%% of outcome='ok' calls returned status 200 with result_field_count = 0 "
                "on days %d-%d, against %.1f%% outside the window (z=%.1f, %d calls in window)"
                % (tool, 100 * w.inside, a, b - 1, 100 * w.outside, w.z, w.n_in),
                "declared tool_error_rate on %s is flat across the break: %.3f in window vs %.3f "
                "outside — the fault is invisible to the error metric" % (tool, err_in, err_out),
                "same-tool retry (retry_count > 0 on a second %s call in the session) in %s of "
                "affected sessions vs %s before the window" % (tool, _fmt(retry_in), _fmt(retry_pre)),
                "cohort resolution_rate %s in window vs %s baseline (%s)"
                % (_fmt(observed, 4), _fmt(expected, 4), baseline_note),
            ]
            if change:
                evidence.append(format_change(change))
            else:
                evidence.append("no kind=tool config change on %s within %d days of the onset — "
                                "cause proven by mechanism, change not attributed" % (tool, T.ALIGN_BEFORE))
            out.append({
                "kind": "tool_contract", "tenant": tenant, "cohort": cohort,
                "metric": "resolution_rate", "a": a, "b": b,
                "observed": observed, "expected": expected, "baseline_note": baseline_note,
                "rows_in": cohort_in, "baseline_rows": baseline_rows, "z": w.z,
                "mechanism": {"empty_success_rate_in": w.inside, "empty_success_rate_out": w.outside,
                              "tool_error_rate_in": err_in, "tool_error_rate_out": err_out,
                              "same_tool_retry_in": retry_in, "tool": tool},
                "evidence": evidence,
                "cause_class": "tool.contract_break",
                "confidence": 0.9 if change else 0.75,
                "attributed_change": change,
                "diag_evidence": [
                    "status_code 200, outcome 'ok', result_field_count 0: transport success, "
                    "application failure — the agent received nothing to answer with",
                    "ordinary error_class taxonomy never fires, so alerting on tool_error_rate misses it",
                    "the runtime's own same-tool retry inside the session corroborates the empty answer",
                ] + (["onset aligns with the %s %s -> %s deploy on day %d" % (
                    change["target"], change.get("from_value"), change.get("to_value"), change["day"])]
                     if change else []),
            })
    return out


# ------------------------------------------------------------- resolution ---

def _kb_stats(rows: List[dict]):
    lookups = sum(s["derived"]["n_kb_lookups"] for s in rows)
    hits = sum(s["derived"]["n_kb_hits"] for s in rows)
    scores = [x for s in rows for x in s["derived"]["kb_scores"]]
    with_kb = sum(1 for s in rows if s["derived"]["n_kb_lookups"])
    return {"lookup_share": rate(with_kb, len(rows)), "hit_rate": rate(hits, lookups),
            "score_median": median(scores), "score_p10": percentile(scores, 0.10),
            "score_p90": percentile(scores, 0.90), "n_lookups": lookups}


def detect_resolution_drops(corpus: Corpus, cov: Coverage, claimed: List[dict]) -> List[dict]:
    """(tenant, intent) cohorts whose resolution falls; mechanism decides the class."""
    D = corpus.days
    out: List[dict] = []
    for tenant in corpus.tenants:
        rows_t = list(corpus.rows(tenant))
        by_intent: Dict[str, List[dict]] = defaultdict(list)
        for s in rows_t:
            by_intent[s["intent"]].append(s)
        for intent, rows in by_intent.items():
            if len(rows) < 2 * T.RES_MIN_N_IN:
                continue
            ser = DaySeries(D)
            for s in rows:
                ser.add(int(s["day"]), 1.0 if resolved(s) else 0.0)
            w = best_window(ser, "down", min_len=3, max_len=D, min_n_in=T.RES_MIN_N_IN,
                            min_n_out=T.RES_MIN_N_IN, min_days_out=T.MIN_DAYS_OUT)
            if not w or (w.outside - w.inside) < T.RES_MIN_DROP or w.z < T.RES_MIN_Z:
                continue
            a, b = w.a, w.b
            first = ser.first_day()
            # A cohort whose traffic begins at/after the window is "born broken": it has
            # no before-period. Open the window on its first traffic, or the launch
            # change that created it, rather than on the first day that cleared
            # best_window's minimum sample size — that gap is what creates detection lag.
            is_new = (first is not None and first >= a - T.NEW_COHORT_LOOKBACK
                      and a >= T.MIN_DAYS_OUT)
            launch = None
            if is_new:
                launch = corpus.nearest_change(tenant, a, {"kb"}, before=T.NEW_COHORT_LOOKBACK,
                                               after=T.ALIGN_AFTER)
                if launch and launch["day"] < a:
                    a = launch["day"]
                elif first < a:
                    a = first
            if any(c["tenant"] == tenant and c["cohort"].get("intent") == intent
                   and not (b <= c["a"] or a >= c["b"]) for c in claimed):
                continue                                 # owned by the tool detector

            in_rows = _in_rows(rows, a, b)
            kb_in = _kb_stats(in_rows)
            tool_share = rate(sum(1 for s in in_rows if s["derived"]["n_tool_calls"]),
                              len(in_rows)) or 0.0

            # peers: same tenant, same window, other KB-backed intents served by the
            # same agents. Never another tenant.
            agents = {s["agent_id"] for s in in_rows}
            peer_rows: List[dict] = []
            peer_names: List[str] = []
            for p_intent, p_rows in by_intent.items():
                if p_intent == intent:
                    continue
                p_in = [s for s in _in_rows(p_rows, a, b) if s["agent_id"] in agents]
                if len(p_in) < T.RES_MIN_N_IN:
                    continue
                if (_kb_stats(p_in)["lookup_share"] or 0.0) < T.KB_LOOKUP_SHARE:
                    continue
                peer_rows.extend(p_in)
                peer_names.append(p_intent)
            kb_peer = _kb_stats(peer_rows) if peer_rows else {}

            pre = _pre_rows(rows, a)
            if len(pre) >= T.RES_MIN_N_IN:
                baseline_rows = pre
                baseline_note = "this intent's own resolution over the %d days before the break" % T.LOOKBACK
            elif peer_rows:
                baseline_rows = peer_rows
                baseline_note = ("this cohort has no before-period, so the baseline is the other "
                                 "KB-backed intents on the same agents in the same tenant and the "
                                 "same days (%s)" % ", ".join(sorted(peer_names)))
            else:
                baseline_rows = [s for s in rows if not (a <= int(s["day"]) < b)]
                baseline_note = "this intent's own resolution outside the window"
            observed, expected = _res_rate(in_rows), _res_rate(baseline_rows)

            kb_mech = ((kb_in["lookup_share"] or 0.0) >= T.KB_LOOKUP_SHARE
                       and (kb_in["hit_rate"] if kb_in["hit_rate"] is not None else 1.0) <= T.KB_HIT_LOW
                       and (not kb_peer or (kb_peer.get("hit_rate") or 0.0) >= T.KB_HIT_PEER_OK))
            change = None
            from_day = a
            evidence = [
                "%s resolution_rate %s on days %d-%d against %s (%s); z=%.1f, n=%d"
                % (intent, _fmt(observed, 4), a, b - 1, _fmt(expected, 4), baseline_note, w.z, len(in_rows)),
            ]
            if is_new:
                evidence.append("the cohort has no traffic before day %d — it was born broken, so "
                                "its own history cannot be the baseline" % first)
            if kb_mech:
                cause = "kb.gap"
                change = corpus.nearest_change(tenant, a, {"kb"}, before=T.ALIGN_BEFORE, after=T.ALIGN_AFTER)
                if launch and first is not None and launch["day"] == a:
                    evidence.append("window opens on day %d, the launch change that created the "
                                    "cohort; first traffic arrives on day %d" % (a, first))
                evidence.append("kb_hit is true on %s of %d lookups in the cohort; kb_top_score median "
                                "%s (p10-p90 %s-%s)" % (_fmt(kb_in["hit_rate"]), kb_in["n_lookups"],
                                                        _fmt(kb_in["score_median"]), _fmt(kb_in["score_p10"]),
                                                        _fmt(kb_in["score_p90"])))
                if kb_peer:
                    evidence.append("peer KB-backed intents on the same agents in the same window: "
                                    "kb_hit %s, kb_top_score median %s, resolution %s"
                                    % (_fmt(kb_peer["hit_rate"]), _fmt(kb_peer["score_median"]),
                                       _fmt(_res_rate(peer_rows), 4)))
                evidence.append("tool calls in %.0f%% of cohort sessions — %s"
                                % (100 * tool_share, "tool causes excluded" if tool_share < T.TOOL_INVOLVED_SHARE
                                   else "a tool is involved; checked separately"))
                confidence = 0.9 if change else 0.75
                diag = [
                    "retrieval returned no confident document on essentially every lookup, so the "
                    "agent fell through to a generic reply",
                    "peer intents on the same agents and the same days are unaffected, so the agent, "
                    "model and prompt are not the cause",
                ]
            else:
                if (w.outside - w.inside) < T.UNKNOWN_MIN_DROP or w.z < T.UNKNOWN_MIN_Z:
                    continue                          # not strong enough to report blind
                cause = "unknown"
                confidence = 0.4
                evidence.append("kb_hit %s, tool involvement %.0f%%, empty-success not elevated: no "
                                "mechanism proven" % (_fmt(kb_in["hit_rate"]), 100 * tool_share))
                diag = ["outcome moved but no logged mechanism explains it; no change is blamed"]
            if change:
                evidence.append(format_change(change))
                diag.append("the %s change on day %d ('%s') is the nearest marker; the launch shipped "
                            "without retrievable coverage for this cohort" % (
                                change["kind"], change["day"], change.get("note", "")))
            elif cause == "kb.gap":
                evidence.append("no kind=kb config change within %d days of onset — the gap is proven "
                                "by retrieval telemetry, not by a marker" % T.ALIGN_BEFORE)
            out.append({
                "kind": "kb_gap" if kb_mech else "resolution_drop", "tenant": tenant,
                "cohort": {"intent": intent}, "metric": "resolution_rate",
                "a": from_day, "b": b, "observed": observed, "expected": expected,
                "baseline_note": baseline_note, "rows_in": in_rows, "baseline_rows": baseline_rows,
                "peer_intents": sorted(peer_names), "is_new": is_new, "z": w.z,
                "mechanism": {"kb": kb_in, "kb_peer": kb_peer, "tool_share": tool_share},
                "evidence": evidence, "cause_class": cause, "confidence": confidence,
                "attributed_change": change, "diag_evidence": diag,
            })
    return out


# ------------------------------------------------------------- efficiency ---

def detect_efficiency(corpus: Corpus, cov: Coverage) -> List[dict]:
    """Turns-to-resolve rises on one agent while outcomes stay flat."""
    D = corpus.days
    out: List[dict] = []
    for tenant in corpus.tenants:
        rows_t = list(corpus.rows(tenant))
        by_agent: Dict[str, List[dict]] = defaultdict(list)
        for s in rows_t:
            by_agent[s["agent_id"]].append(s)
        for agent, rows in by_agent.items():
            res_rows = [s for s in rows if resolved(s)]
            if len(res_rows) < 2 * T.EFF_MIN_N_IN:
                continue
            ser = DaySeries(D)
            for s in res_rows:
                ser.add(int(s["day"]), float(s["turns"]))
            w = best_window(ser, "up", min_len=4, max_len=D, min_n_in=T.EFF_MIN_N_IN,
                            min_n_out=T.EFF_MIN_N_IN, min_days_out=T.MIN_DAYS_OUT)
            if not w or w.outside <= 0:
                continue
            ratio = w.inside / w.outside
            if ratio < T.EFF_MIN_RATIO or w.z < T.EFF_MIN_Z:
                continue
            a, b = w.a, w.b

            # peer control: sibling agents in the same tenant over the same days
            peers = {}
            for p_agent, p_rows in by_agent.items():
                if p_agent == agent:
                    continue
                p_in = [float(s["turns"]) for s in _in_rows(p_rows, a, b) if resolved(s)]
                p_out = [float(s["turns"]) for s in p_rows if resolved(s) and not (a <= int(s["day"]) < b)]
                if len(p_in) >= T.EFF_MIN_N_IN and len(p_out) >= T.EFF_MIN_N_IN:
                    peers[p_agent] = mean(p_in) / mean(p_out)
            worst_peer = max(peers.values()) if peers else 1.0
            if peers and (worst_peer - 1.0) > T.EFF_PEER_TOLERANCE * (ratio - 1.0):
                continue                           # the whole tenant moved: load or mix, not this agent

            # stratify by intent so a mix shift toward long intents cannot fake a rise
            in_rows = _in_rows(rows, a, b)
            top_intents = [i for i, _ in Counter(s["intent"] for s in in_rows).most_common(3)]
            strata = {}
            for it in top_intents:
                t_in = [float(s["turns"]) for s in in_rows if s["intent"] == it and resolved(s)]
                t_out = [float(s["turns"]) for s in rows if s["intent"] == it and resolved(s)
                         and not (a <= int(s["day"]) < b)]
                if len(t_in) >= 20 and len(t_out) >= 20:
                    strata[it] = mean(t_in) / mean(t_out)
            if strata and sum(1 for r in strata.values() if r >= 1.10) < max(1, (len(strata) + 1) // 2):
                continue

            pre = _pre_rows(rows, a)
            base_rows = pre if len(pre) >= T.EFF_MIN_N_IN else [s for s in rows if not (a <= int(s["day"]) < b)]
            base_note = ("this agent's own sessions over the %d days before the break" % T.LOOKBACK
                         if base_rows is pre else "this agent's own sessions outside the window")
            res_in, res_base = _res_rate(in_rows), _res_rate(base_rows)
            med_in = median([float(s["turns"]) for s in in_rows if resolved(s)])
            med_base = median([float(s["turns"]) for s in base_rows if resolved(s)])
            mean_in = mean([float(s["turns"]) for s in in_rows if resolved(s)])
            mean_base = mean([float(s["turns"]) for s in base_rows if resolved(s)])
            cost_in = mean([float(s["cost_usd"]) for s in in_rows
                            if cov.covered("llm_call", s) and s.get("cost_usd") is not None])
            cost_base = mean([float(s["cost_usd"]) for s in base_rows
                              if cov.covered("llm_call", s) and s.get("cost_usd") is not None])
            outcome_flat = res_in is not None and res_base is not None and abs(res_in - res_base) <= T.OUTCOME_FLAT
            # attribute by what actually changed in the data, not by which config
            # marker happens to sit closest to an onset day an innocent change
            # may share.
            prompt_changed, model_changed, pv, md = _changed_dimension(in_rows, base_rows)
            if prompt_changed:
                cause = "prompt.regression"
                change = corpus.nearest_change(tenant, a, {"prompt"}, target=agent,
                                               before=T.ALIGN_BEFORE, after=T.ALIGN_AFTER)
            elif model_changed:
                cause = "model.change"
                change = corpus.nearest_change(tenant, a, {"model"}, target=agent,
                                               before=T.ALIGN_BEFORE, after=T.ALIGN_AFTER)
            else:
                change = corpus.nearest_change(tenant, a, {"prompt", "model"}, target=agent,
                                               before=T.ALIGN_BEFORE, after=T.ALIGN_AFTER)
                cause = ("prompt.regression" if change and change["kind"] == "prompt"
                         else "model.change" if change and change["kind"] == "model" else "unknown")
            evidence = [
                "%s median turns to resolve %s -> %s (mean %s -> %s, +%.0f%%) on days %d-%d vs %s"
                % (agent, _fmt(med_base, 1), _fmt(med_in, 1), _fmt(mean_base, 2), _fmt(mean_in, 2),
                   100 * (mean_in / mean_base - 1) if mean_base else 0, a, b - 1, base_note),
                "resolution_rate %s -> %s: outcome %s, so threshold alerts on outcomes never fire"
                % (_fmt(res_base, 4), _fmt(res_in, 4), "flat" if outcome_flat else "also moved"),
                "cost per session %s -> %s USD (%s), recorded llm_call cost, coverage-limited to the "
                "agent_kind that emits cost" % (_fmt(cost_base, 5), _fmt(cost_in, 5),
                                                "+%.0f%%" % (100 * (cost_in / cost_base - 1)) if cost_in and cost_base else "n/a"),
                "config dimension that changed vs baseline — prompt_version %s -> %s, model %s -> %s"
                % (_fmt_set(pv[0]), _fmt_set(pv[1]), _fmt_set(md[0]), _fmt_set(md[1])),
                "peer control — sibling agents in the same tenant over the same days: %s"
                % (", ".join("%s x%.2f" % kv for kv in sorted(peers.items())) or "none with enough volume"),
                "within-intent check — the rise holds inside the agent's top intents: %s"
                % (", ".join("%s x%.2f" % kv for kv in sorted(strata.items())) or "n/a"),
            ]
            if change:
                evidence.append(format_change(change))
                diag_attr = "the %s change on %s (%s -> %s, '%s') lands on the onset day" % (
                    change["kind"], agent, change.get("from_value"), change.get("to_value"),
                    change.get("note", ""))
            elif cause != "unknown":
                dim = "prompt_version" if cause == "prompt.regression" else "model"
                evidence.append("no config marker aligned; attribution is by the agent's changed %s between "
                                "baseline and window" % dim)
                diag_attr = ("no config marker aligned, but the agent's %s differs between baseline and window, "
                             "so the cause is attributed by the changed field" % dim)
            else:
                evidence.append("no prompt/model change targeting %s within %d days of onset" % (agent, T.ALIGN_BEFORE))
                diag_attr = "no aligned prompt/model change — mechanism proven, cause left unknown"
            out.append({
                "kind": "efficiency", "tenant": tenant, "cohort": {"agent_id": agent},
                "metric": "turns_to_resolve", "a": a, "b": b,
                "observed": med_in, "expected": med_base, "baseline_note": base_note,
                "rows_in": in_rows, "baseline_rows": base_rows, "z": w.z,
                "mechanism": {"median_in": med_in, "median_base": med_base, "mean_in": mean_in,
                              "mean_base": mean_base, "cost_in": cost_in, "cost_base": cost_base,
                              "res_in": res_in, "res_base": res_base, "peers": peers, "strata": strata,
                              "ratio": ratio},
                "evidence": evidence, "cause_class": cause,
                "confidence": 0.85 if change else 0.6, "attributed_change": change,
                "diag_evidence": [
                    "only this agent moves; sibling agents on the same tenant and days do not, which "
                    "rules out load, mix and tenant-wide model effects",
                    "the rise is present within each major intent, so it is not a change in what "
                    "people asked",
                    "outcomes are flat: the agent still resolves, it just takes longer and costs more",
                    diag_attr,
                ],
            })
    return out


def detect_all(corpus: Corpus, cov: Coverage) -> List[dict]:
    tool_obs = detect_tool_contract(corpus, cov)
    res_obs = detect_resolution_drops(corpus, cov, claimed=tool_obs)
    eff_obs = detect_efficiency(corpus, cov)
    obs = tool_obs + res_obs + eff_obs
    obs.sort(key=lambda o: (o["a"], o["tenant"]))
    return obs
