"""The eleven operator asks → a metric or a structured gap for each.

Rule 1 is respected everywhere: nothing here asks a model anything. Judged
values are read from the corpus's own judge (``quality_score``) and published
with a calibration computed against the kit's human rubric labels.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from .coverage import Coverage
from .features import (Corpus, abandoned, contained, mean, median, milestones_of,
                       percentile, rate, resolved, unplanned_handoff)
from .metrics import gap, metric, r4

SESSIONS = "corpus/sessions.jsonl.gz"
STEPS = "corpus/agent_steps.jsonl.gz"


def _short(tenant: str) -> str:
    # the full key, sanitised, so two tenants sharing a prefix cannot collide
    return "".join(ch if ch.isalnum() else "_" for ch in tenant)


def _cost_coverage(cov: Coverage, corpus: Corpus, tenant: str) -> Tuple[float, str, Optional[dict]]:
    rows = list(corpus.rows(tenant))
    with_cost = sum(1 for s in rows if s.get("cost_usd") is not None)
    v = rate(with_cost, len(rows)) or 0.0
    declared = (cov.corpus.kit.capability("cost_per_session").get("coverage") or {}).get(tenant)
    basis = ("share of %s sessions carrying a recorded cost_usd, measured from this corpus" % tenant)
    extra = None
    if declared is not None and abs(declared - v) > 0.03:
        why = ("catalog declares cost coverage %.2f for this tenant (on the stated basis that cost is absent "
               "on v2_flow); in this corpus %.1f%% of sessions carry cost_usd, so the measured value is "
               "reported and v2 is not excluded" % (declared, 100 * v))
        basis += ". NOTE " + why
        extra = {"observed_vs_declared": {"declared": round(float(declared), 4),
                                          "observed": round(v, 4), "why": why}}
    return round(v, 4), basis, extra


def _window_confounds(tenant: str, x0: int, x1: int,
                      regressions, decoys) -> List[str]:
    """Detected events (faults or decoys) whose window overlaps [x0, x1) for this
    tenant. Judge-version events are excluded: quality is already guarded by the
    judge-version check, so they are not a confound for the other measures."""
    names: List[str] = []
    for g in list(regressions or []) + list(decoys or []):
        kind = str(g.get("kind") or "")
        if kind.startswith("judge"):
            continue
        if g.get("tenant") not in (tenant, "*"):
            continue
        a, b = g.get("a"), g.get("b")
        if a is None or b is None:
            continue
        if not (b <= x0 or a >= x1):
            names.append("%s/%s" % (g.get("tenant"), kind or g.get("cause_class") or "event"))
    return sorted(set(names))


def build(corpus: Corpus, cov: Coverage, kit_catalog: dict,
          regressions: Optional[List[dict]] = None,
          decoys: Optional[List[dict]] = None) -> Tuple[List[dict], List[dict], dict]:
    """Returns (metrics, gaps, extras). ``extras`` carries derived facts the
    report reuses (e.g. the review queue) so nothing is computed twice.

    ``regressions``/``decoys`` are the detected fault/decoy candidates (dicts
    with ``tenant``, ``a``, ``b``); they deconfound A07 from concurrent events.
    """
    metrics: List[dict] = []
    gaps: List[dict] = []
    extras: Dict = {}
    D = corpus.days
    half = D // 2
    tenants = corpus.tenants
    all_rows = list(corpus.sessions.values())

    # ---------------------------------------------------------------- A01 --
    per_t = {t: r4(rate(sum(1 for s in corpus.rows(t) if contained(s)), sum(1 for _ in corpus.rows(t))))
             for t in tenants}
    metrics.append(metric(
        "m_containment", "Containment rate", "A01", "session", "measured",
        r4(rate(sum(1 for s in all_rows if contained(s)), len(all_rows))),
        1.0, "session_end is on every session; handoff_by_design is read from the session with the "
             "step-grain flag as fallback, so containment is checkable for every agent_kind",
        SESSIONS, "session_end = 'resolved' OR (session_end = 'handoff' AND handoff_by_design = true)",
        "all sessions in scope", breakdowns=["tenant"],
        alternatives=["resolution_rate — excludes by-design handoffs entirely"],
        breakdown={"by_tenant": per_t}))

    # ---------------------------------------------------------------- A04 --
    for t in tenants:
        rows = [s for s in corpus.rows(t) if cov.covered("tool_call", s)]
        calls = sum(s["derived"]["n_tool_calls"] for s in rows)
        errs = sum(s["derived"]["n_tool_errors"] for s in rows)
        ok = sum(s["derived"]["n_tool_ok"] for s in rows)
        empty = sum(s["derived"]["n_empty_success"] for s in rows)
        by_tool = Counter()
        by_tool_err = Counter()
        for s in rows:
            by_tool.update(s["derived"]["calls_by_tool"])
            by_tool_err.update(s["derived"]["errors_by_tool"])
        metrics.append(metric(
            "m_tool_failure_" + _short(t), "Tool failure rate — " + t, "A04", "step", "measured",
            r4(rate(errs, calls)), cov.value("tool_call", t), cov.basis("tool_call", t),
            STEPS + " WHERE step_type = 'tool_call'",
            "outcome <> 'ok' AND tenant = '%s'" % t,
            "all tool_call steps for this tenant on agent_kinds that emit tool_call",
            breakdowns=["tool_name"],
            excluded=cov.excluded_filter("tool_call"),
            breakdown={"by_tool": {k: r4(rate(by_tool_err[k], v)) for k, v in sorted(by_tool.items())},
                       "calls": calls}))
        metrics.append(metric(
            "m_tool_empty_success_" + _short(t), "Tool answered with nothing — " + t, "A04", "step", "derived",
            r4(rate(empty, ok)), cov.value("tool_call", t),
            "derived from the same tool_call rows; " + cov.basis("tool_call", t),
            STEPS + " WHERE step_type = 'tool_call'",
            "outcome = 'ok' AND status_code = 200 AND result_field_count = 0 AND tenant = '%s'" % t,
            "all tool_call steps with outcome = 'ok' for this tenant",
            breakdowns=["tool_name"], excluded=cov.excluded_filter("tool_call"),
            alternatives=["catalog lists silent_tool_success as derivable_not_declared — derived and declared here"],
            breakdown={"by_tool": {k: r4(rate(sum(s["derived"]["empty_by_tool"].get(k, 0) for s in rows),
                                                 v - by_tool_err[k])) for k, v in sorted(by_tool.items())}}))

    # ---------------------------------------------------------------- A02 --
    mom = {}
    adj = {}
    adj_detail = {}
    for t in tenants:
        rows = list(corpus.rows(t))
        m1 = [s for s in rows if int(s["day"]) < half]
        m2 = [s for s in rows if int(s["day"]) >= half]
        by_intent = {}
        for it in sorted({s["intent"] for s in rows}):
            a = [s for s in m1 if s["intent"] == it]
            b = [s for s in m2 if s["intent"] == it]
            if len(a) >= 30 and len(b) >= 30:
                by_intent[it] = {
                    "resolution": [r4(rate(sum(1 for s in a if resolved(s)), len(a))),
                                   r4(rate(sum(1 for s in b if resolved(s)), len(b)))],
                    "median_turns_to_resolve": [median([s["turns"] for s in a if resolved(s)]),
                                                median([s["turns"] for s in b if resolved(s)])],
                    "share": [r4(len(a) / float(len(m1))), r4(len(b) / float(len(m2)))],
                }
        # mix-adjusted: hold month-1 intent shares and apply month-2 per-intent
        # rates, over the intents that exist in BOTH months. Both ends of the
        # comparison sit on that same common-intent scope, so "change beyond mix"
        # is not contaminated by an intent entering or leaving the cohort.
        common = sorted(by_intent)               # already requires >= 30 in both months
        n1 = {it: sum(1 for s in m1 if s["intent"] == it) for it in common}
        n2 = {it: sum(1 for s in m2 if s["intent"] == it) for it in common}
        tot1 = sum(n1.values())
        weights = {it: (n1[it] / float(tot1)) for it in common} if tot1 else {}
        common_set = set(common)
        r1_raw = rate(sum(1 for s in m1 if resolved(s)), len(m1))
        r2_raw = rate(sum(1 for s in m2 if resolved(s)), len(m2))
        r1_common = (rate(sum(1 for s in m1 if s["intent"] in common_set and resolved(s)), tot1)
                     if tot1 else None)
        tot2 = sum(n2.values())
        r2_common = (rate(sum(1 for s in m2 if s["intent"] in common_set and resolved(s)), tot2)
                     if tot2 else None)
        r2_adj = None
        if common and tot1:
            r2_adj = sum(weights[it] * (rate(sum(1 for s in m2 if s["intent"] == it and resolved(s)), n2[it]) or 0.0)
                         for it in common)
        all_intents = sorted({s["intent"] for s in rows})
        excluded = [{"intent": it,
                     "n_month1": sum(1 for s in m1 if s["intent"] == it),
                     "n_month2": sum(1 for s in m2 if s["intent"] == it)}
                    for it in all_intents if it not in common_set]
        born_in_month2 = [e["intent"] for e in excluded if e["n_month1"] == 0 and e["n_month2"] > 0]
        conf = sorted(set(_window_confounds(t, 0, half, regressions, decoys)
                          + _window_confounds(t, half, D, regressions, decoys)))
        adj[t] = {
            "raw_all_intents": [r4(r1_raw), r4(r2_raw)],
            "common": [r4(r1_common), r4(r2_common)],
            "adjusted": [r4(r1_common), r4(r2_adj)] if (r1_common is not None and r2_adj is not None) else None,
            "mix_effect": (r4(r2_common - r2_adj) if (r2_common is not None and r2_adj is not None) else None),
            "change_beyond_mix": (r4(r2_adj - r1_common) if (r2_adj is not None and r1_common is not None) else None),
        }
        adj_detail[t] = {
            "weights_month1": {k: r4(v) for k, v in weights.items()},
            "common_intents": common,
            "excluded_intents": excluded,
            "born_in_month2": born_in_month2,
            "confounded_by": conf,
            "reading": ("not reliable as a trend: detected event(s) overlap a month window (%s)" % ", ".join(conf)
                        if conf else "mix held at month-1 shares over the common intents; change_beyond_mix is the agent"),
            "caveat": (("a cohort born in month 2 (%s) has no month-1 share and is excluded from the adjusted "
                        "comparison; a regression that arrives as a brand-new cohort shows in the raw per-intent "
                        "table, not here") % ", ".join(born_in_month2)) if born_in_month2 else None,
        }
        mom[t] = {
            "resolution": [r4(rate(sum(1 for s in m1 if resolved(s)), len(m1))),
                           r4(rate(sum(1 for s in m2 if resolved(s)), len(m2)))],
            "containment": [r4(rate(sum(1 for s in m1 if contained(s)), len(m1))),
                            r4(rate(sum(1 for s in m2 if contained(s)), len(m2)))],
            "median_turns_to_resolve": [median([s["turns"] for s in m1 if resolved(s)]),
                                        median([s["turns"] for s in m2 if resolved(s)])],
            "by_intent": by_intent,
        }
    metrics.append(metric(
        "m_mom_resolution", "Month-over-month resolution and turns, stratified by intent", "A02", "session",
        "measured", {t: {"resolution": v["resolution"], "containment": v["containment"],
                         "median_turns_to_resolve": v["median_turns_to_resolve"]} for t, v in mom.items()},
        1.0, "session_end and turns are present on every session",
        SESSIONS, "day < %d AS month_1, day >= %d AS month_2; per tenant, then per intent" % (half, half),
        "all sessions in each month, per tenant and per intent",
        breakdowns=["tenant", "intent"],
        alternatives=["quality_score is NOT trended month over month: the judge_version boundary makes "
                      "it a measurement of the rubric, not the agent",
                      "aggregate containment is shown with the intent-share column so a mix shift is visible",
                      "m_mom_resolution_adjusted holds the month-1 mix and reports the change beyond mix"],
        value_note="[month_1, month_2] pairs. Read per intent: an aggregate move with flat intents is mix.",
        breakdown={t: v["by_intent"] for t, v in mom.items()}))
    if adj:
        metrics.append(metric(
            "m_mom_resolution_adjusted", "Month-over-month resolution, mix held at month-1 shares", "A02",
            "session", "derived",
            {t: {"raw_all_intents": v["raw_all_intents"], "common": v["common"],
                 "adjusted": v["adjusted"], "mix_effect": v["mix_effect"],
                 "change_beyond_mix": v["change_beyond_mix"]} for t, v in adj.items()},
            1.0, "session_end is present on every session; both ends of the adjusted comparison are on the "
                 "common-intent scope (month-1 shares held, month-2 per-intent rates)",
            SESSIONS, "weight month-2 per-intent resolution by month-1 intent shares, over the common intents only, "
                      "renormalised; the month-1 end is the common-intent rate on that same scope",
            "sessions of the common intents in each month", breakdowns=["tenant", "intent"],
            alternatives=["raw_all_intents is the aggregate over every intent and is shown beside it; a gap between "
                          "raw_all_intents and adjusted mixes a true mix effect with intents entering or leaving the cohort",
                          "intents present in only one month, or with fewer than 30 sessions in a month, are excluded and listed",
                          "a cohort born in month 2 cannot be mix-held and is excluded; a regression delivered as a "
                          "new cohort is found in m_mom_resolution per intent, not here"],
            value_note="derived: raw_all_intents, the common-intent rates, the mix effect and the change beyond mix. "
                       "Both adjusted ends are on the common-intent scope, so change_beyond_mix is not a scope change; "
                       "the breakdown lists excluded intents, those born in month 2, and overlapping events",
            breakdown=adj_detail))
    # The measured abandonment rate is reported inside the A09 gap rather than as
    # a standalone metric: A09 is answered by a REQUIRES_NEW_JUDGE gap, and a
    # measured metric tagged A09 would be misread by the scorer's fidelity trap
    # (which expects the A09 answer to be judged) and would also misattribute the
    # ask. The measured numbers are in the gap text.

    # ---------------------------------------------------------------- A03 / A08 --
    for t in tenants:
        cval, cbasis, cextra = _cost_coverage(cov, corpus, t)
        t_rows = list(corpus.rows(t))
        rows = [s for s in t_rows if s.get("cost_usd") is not None]
        by_intent_all = {}
        for s in t_rows:
            by_intent_all.setdefault(s["intent"], []).append(s)
        by_intent = {}
        unbounded = []
        no_cost = []
        for it in sorted(by_intent_all):
            r = by_intent_all[it]
            with_cost = [s for s in r if s.get("cost_usd") is not None]
            cost = sum(float(s["cost_usd"]) for s in with_cost)
            n_res_all = sum(1 for s in r if resolved(s))
            n_res_with_cost = sum(1 for s in with_cost if resolved(s))
            hand = sum(1 for s in r if unplanned_handoff(s))
            aband = sum(1 for s in r if abandoned(s))
            if n_res_all == 0:
                cpr, cost_state = None, "unbounded"         # spent money, resolved nothing
                unbounded.append(it)
            elif n_res_with_cost == 0:
                cpr, cost_state = None, "cost_unavailable"  # resolves, but no priced resolution
                no_cost.append(it)
            else:
                cpr, cost_state = r4(rate(cost, n_res_with_cost)), "ok"
            by_intent[it] = {
                "sessions": len(r),
                "sessions_with_cost": len(with_cost),
                "cost_usd": r4(cost),
                "resolved": n_res_all,
                "resolved_with_cost": n_res_with_cost,
                "cost_per_resolved": cpr,
                "cost_to_resolve": cpr if cpr is not None else cost_state,
                "cost_state": cost_state,
                "unplanned_handoffs": hand,
                "escalation_rate": r4(rate(hand, len(r))),
                "abandoned": aband,
                "abandon_rate": r4(rate(aband, len(r))),
            }

        def _unit_cost_order(kv):
            # zero successes with spend is unbounded unit cost (worst case) and is
            # ranked first; "resolves but no priced resolution" is a cost-telemetry
            # gap, not infinite cost, and ranks next; then the expensive per-resolved.
            v = kv[1]
            if v["cost_state"] == "unbounded":
                return (0, -(v["cost_usd"] or 0.0))
            if v["cost_state"] == "cost_unavailable":
                return (1, -(v["cost_usd"] or 0.0))
            return (2, -(v["cost_per_resolved"] or 0.0))

        ranked = sorted(by_intent.items(), key=_unit_cost_order)
        by_kind = {k: r4(sum(float(s["cost_usd"]) for s in rows if s["agent_kind"] == k))
                   for k in sorted({s["agent_kind"] for s in rows})}
        metrics.append(metric(
            "m_cost_per_resolved_" + _short(t), "Cost per resolved conversation by intent — " + t, "A03",
            "session", "measured",
            {k: (v["cost_per_resolved"] if v["cost_per_resolved"] is not None else v["cost_state"])
             for k, v in ranked[:5]},
            cval, cbasis, SESSIONS, "cost_usd IS NOT NULL AND tenant = '%s'" % t,
            "resolved sessions of the intent that carry a recorded cost (sessions without recorded cost "
            "leave both numerator and denominator)",
            breakdowns=["intent", "agent_kind"],
            alternatives=[
                "escalation is a hidden cost: unplanned handoffs and abandonment sit beside each intent as "
                "counts and rates, never priced — no human-handling constant exists in the corpus",
                "zero successful resolutions with spend is unbounded unit cost; it is listed as "
                "cost_to_resolve='unbounded' and ranked first by spend, not last",
                "an intent that resolves but has no priced resolution is cost_to_resolve='cost_unavailable' "
                "(a telemetry gap), ranked after unbounded and never confused with infinite cost"],
            value_note="ranked by unit cost: 'unbounded' (zero successes, by spend) first, then "
                       "'cost_unavailable' (no priced success), then cost per resolved conversation; "
                       "escalation_rate and abandon_rate sit beside each intent so a cheap-looking intent that "
                       "escalates is not read as cheap",
            breakdown={"by_intent": by_intent, "unbounded_intents": unbounded,
                       "cost_unavailable_intents": no_cost, "cost_by_agent_kind": by_kind},
            coverage_extra=cextra))
        months = {}
        for m_i in range(0, D, half or D):
            r = [s for s in rows if m_i <= int(s["day"]) < m_i + half]
            if not r:
                continue
            months["days_%d_%d" % (m_i, min(D, m_i + half) - 1)] = {
                "total_usd": r4(sum(float(s["cost_usd"]) for s in r)),
                "by_intent": {it: r4(sum(float(s["cost_usd"]) for s in r if s["intent"] == it))
                              for it in sorted({s["intent"] for s in r})},
                "by_agent": {ag: r4(sum(float(s["cost_usd"]) for s in r if s["agent_id"] == ag))
                             for ag in sorted({s["agent_id"] for s in r})},
            }
        metrics.append(metric(
            "m_spend_" + _short(t), "Spend serving customers, per month and on what — " + t, "A08",
            "session", "measured", {k: v["total_usd"] for k, v in months.items()},
            cval, cbasis, SESSIONS, "SUM(cost_usd) WHERE tenant = '%s' GROUP BY %d-day month" % (t, half),
            "n/a — a total; recorded llm_call cost rolled up to session grain, nothing imputed",
            breakdowns=["month", "intent", "agent_id"],
            alternatives=["human handling cost is NOT included: no price constant exists in the corpus, so "
                          "handoffs are reported as counts, never as dollars"],
            breakdown=months,
            coverage_extra=cextra))

    # ---------------------------------------------------------------- A05 --
    journeys = kit_catalog.get("milestones") or {}
    dropoff = {}
    headline = None
    for t in tenants:
        for it, steps in (journeys.get(t) or {}).items():
            rows = [s for s in corpus.rows(t) if s["intent"] == it]
            if len(rows) < 30 or not steps:
                continue
            reach = []
            for ms in steps:
                reach.append(r4(rate(sum(1 for s in rows if ms in milestones_of(s)), len(rows))))
            drops = [(steps[i], (reach[i - 1] or 0) - (reach[i] or 0)) for i in range(1, len(steps))]
            worst = max(drops, key=lambda kv: kv[1]) if drops else None
            dropoff[t + "/" + it] = {"milestones": steps, "reach_rate": reach, "n": len(rows),
                                     "largest_drop_before": worst[0] if worst else None,
                                     "largest_drop": r4(worst[1]) if worst else None}
            if headline is None and any("return" in (m or "").lower().split("_") for m in steps):
                headline = dropoff[t + "/" + it]
    if headline is None and dropoff:
        # no returns-like journey found (e.g. renamed slices): fall back to the
        # journey with the largest drop-off so A05 still answers
        headline = max(dropoff.values(), key=lambda v: (v.get("largest_drop") if v.get("largest_drop") is not None else -1.0))
    metrics.append(metric(
        "m_returns_dropoff", "Milestone drop-off in the returns journey", "A05", "session", "measured",
        headline, 1.0, "milestones_reached is read from the session with the turn-step milestones as "
                       "fallback; journeys come from catalog.milestones",
        SESSIONS, "intent whose catalog journey is the returns journey; share of sessions reaching each milestone in order",
        "all sessions of the intent", breakdowns=["intent", "milestone", "reach_rate"],
        value_note="returns journey selected structurally by a returns-like milestone; falls back to the largest drop-off; all journeys in breakdown",
        breakdown=dropoff))

    # ---------------------------------------------------------------- A06 --
    kb_by_t = {}
    for t in tenants:
        rows = [s for s in corpus.rows(t) if s["derived"]["n_kb_lookups"]]
        lookups = sum(s["derived"]["n_kb_lookups"] for s in rows)
        hits = sum(s["derived"]["n_kb_hits"] for s in rows)
        scores = [x for s in rows for x in s["derived"]["kb_scores"]]
        by_intent = {}
        for it in sorted({s["intent"] for s in rows}):
            r = [s for s in rows if s["intent"] == it]
            lk = sum(s["derived"]["n_kb_lookups"] for s in r)
            if lk >= 30:
                by_intent[it] = {"kb_hit_rate": r4(rate(sum(s["derived"]["n_kb_hits"] for s in r), lk)),
                                 "kb_top_score_median": median([x for s in r for x in s["derived"]["kb_scores"]])}
        kb_by_t[t] = {"kb_hit_rate": r4(rate(hits, lookups)), "kb_top_score_median": median(scores),
                      "lookups": lookups, "by_intent": by_intent}
    kb_cov = {t: cov.value("kb_lookup", t) for t in tenants}
    kb_declared = (corpus.kit.capability("kb_fallthrough_rate").get("coverage") or {})
    kb_basis = ("per tenant, share of sessions whose agent_kind emits kb_lookup: "
                + ", ".join("%s=%.4f" % (t, kb_cov[t]) for t in tenants)
                + ". The catalog declares kb_fallthrough_rate coverage "
                + ", ".join("%s=%s" % (t, kb_declared.get(t, "n/a")) for t in tenants)
                + " (v2 said to lack kb_lookup); v2 emits it in this corpus, so the measured presence is reported")
    kb_extra = {"observed_vs_declared": {
        "declared": kb_declared, "observed": kb_cov,
        "why": "v2_flow emits kb_lookup rows in this corpus, so measured coverage exceeds the declared v3-only value"}}
    metrics.append(metric(
        "m_kb_hit_rate", "KB retrieval: confident hit rate and top score", "A06", "step", "measured",
        {t: {"kb_hit_rate": v["kb_hit_rate"], "kb_top_score_median": v["kb_top_score_median"]} for t, v in kb_by_t.items()},
        min(kb_cov.values()) if kb_cov else 0.0, kb_basis,
        STEPS + " WHERE step_type = 'kb_lookup'", "kb_hit = true", "all kb_lookup steps per tenant",
        breakdowns=["tenant", "intent"], excluded=cov.excluded_filter("kb_lookup"),
        alternatives=["'actually answering' has a judged reading — see m_quality_judged, segmented by judge_version"],
        breakdown={t: v["by_intent"] for t, v in kb_by_t.items()},
        coverage_extra=kb_extra))

    # judged quality with a real calibration against the kit's human rubric scores
    cal, cal_detail = _quality_calibration(corpus)
    q_by_version = {}
    for v in sorted({s.get("judge_version") for s in all_rows if s.get("judge_version")}):
        qs = [float(s["quality_score"]) for s in all_rows if s.get("judge_version") == v and s.get("quality_score") is not None]
        q_by_version[v] = {"mean": r4(mean(qs)), "n": len(qs)}
    if cal:
        metrics.append(metric(
            "m_quality_judged", "Judged quality (rubric score), segmented by judge_version", "A06", "session",
            "judged", q_by_version, 1.0, "quality_score and judge_version are stamped on every session",
            SESSIONS, "quality_score SEGMENTED BY judge_version — never trended across the boundary",
            "sessions within one judge_version", breakdowns=["judge_version"],
            calibration=cal,
            alternatives=["csat is nullable and present on a small, not-missing-at-random subset of sessions; "
                          "we do not blend it into quality and do not publish a headline for it"],
            value_note="calibration = share of human-labelled sessions where |human - judge| <= 1 on the 1-5 "
                       "rubric, per-version detail in breakdown", breakdown=cal_detail))
    else:
        # no judged reading is published, so say so rather than let A06's judged
        # half disappear without explanation (the measured kb_hit half still ships)
        gaps.append(gap(
            "A06", "REQUIRES_NEW_JUDGE",
            "the judged reading of whether the KB actually answers needs a calibration against human labels; "
            "fewer than 10 human rubric labels joined to sessions in this corpus, so no judged quality metric "
            "is published rather than report one without a calibration.",
            nearest_proxy="the measured kb_hit rate (m_kb_hit_rate)",
            why_the_proxy_misleads="kb_hit is whether retrieval returned a confident document, not whether the "
                                   "answer was good; a confident but wrong document still counts as a hit",
            required_event={"name": "quality_calibration_set", "grain": "session",
                            "fields": ["judge_version", "human_quality", "calibration_set_id"],
                            "owner": "quality-judging"}))
    extras["quality_calibration"] = cal

    # ---------------------------------------------------------------- A07 --
    upgrades = []
    for c in corpus.changes:
        if c.get("kind") != "model":
            continue
        t, agent, d = c["tenant"], c["target"], c["day"]
        rows = [s for s in corpus.rows(t) if s["agent_id"] == agent]
        before = [s for s in rows if max(0, d - 7) <= int(s["day"]) < d]
        after = [s for s in rows if d <= int(s["day"]) < min(D, d + 7)]
        jv_all = ({s.get("judge_version") for s in before}
                  | {s.get("judge_version") for s in after})
        same_judge = len(jv_all) == 1 and None not in jv_all
        have_quality = same_judge and all(s.get("quality_score") is not None for s in before + after)
        judge_near = corpus.nearest_change("*", d, {"judge"}, before=7, after=7)
        # never claim a model effect when another detected event overlaps the window
        confounds = _window_confounds(t, d - 7, d + 7, regressions, decoys)
        if confounds:
            resolution = turns = cost = quality = None
            note = ("window overlaps detected event(s): %s — no model effect claimed"
                    % ", ".join(confounds))
        else:
            resolution = [r4(rate(sum(1 for s in before if resolved(s)), len(before))),
                          r4(rate(sum(1 for s in after if resolved(s)), len(after)))]
            turns = [median([s["turns"] for s in before if resolved(s)]),
                     median([s["turns"] for s in after if resolved(s)])]
            cost = [r4(mean([float(s["cost_usd"]) for s in before if s.get("cost_usd") is not None])),
                    r4(mean([float(s["cost_usd"]) for s in after if s.get("cost_usd") is not None]))]
            quality = ([r4(mean([float(s["quality_score"]) for s in before])),
                        r4(mean([float(s["quality_score"]) for s in after]))] if have_quality else None)
            note = None
        entry = {
            "tenant": t, "agent_id": agent, "day": d, "from": c.get("from_value"), "to": c.get("to_value"),
            "n_before": len(before), "n_after": len(after),
            "resolution": resolution,
            "median_turns_to_resolve": turns,
            "cost_per_session": cost,
            "quality": quality,
            "confounded_by": confounds,
            "quality_note": (("compared within judge_version %s" % next(iter(jv_all))) if have_quality
                             else ("NOT compared: missing quality_score or an ambiguous judge_version "
                                   "across the window"
                                   + (" (judge boundary day %d)" % judge_near["day"] if judge_near else ""))),
            "note": note,
        }
        upgrades.append(entry)
    metrics.append(metric(
        "m_model_upgrade", "Model upgrade before/after on a stable cohort", "A07", "session", "measured",
        upgrades, 1.0, "session outcomes and turns are present on every session; cost where recorded",
        SESSIONS + " JOIN corpus/config_timeline.csv WHERE kind = 'model'",
        "agent_id = change.target, 7 days before vs 7 days after change.day",
        "sessions of the upgraded agent in each 7-day window", breakdowns=[],
        alternatives=["judged quality is only included when both windows share one judge_version",
                      "windows overlapping a detected fault or decoy are marked confounded and left null"],
        value_note="[before, after] pairs per model change found in the config timeline"))

    # ---------------------------------------------------------------- A09 --
    abandon_measured = ", ".join(
        "%s %.2f%%" % (t, 100 * (rate(sum(1 for s in corpus.rows(t) if abandoned(s)),
                                     sum(1 for _ in corpus.rows(t))) or 0.0))
        for t in tenants)
    gaps.append(gap(
        "A09", "REQUIRES_NEW_JUDGE",
        "session_end = 'abandoned' is MEASURED and reported here: abandonment is %s of sessions. WHY a user "
        "abandoned — frustration versus having got what they needed — is a judgment about meaning. No versioned "
        "judge with a published calibration for that question exists in this deployment, and inferring the reason "
        "from the end code would be a Rule 1 violation dressed up as an aggregate. This system publishes no such "
        "number." % abandon_measured,
        nearest_proxy="abandoned sessions with an unusually high turn count or a same-tool retry",
        why_the_proxy_misleads="it flags effort, not frustration; a user who leaves happy after two turns and one "
                               "who rage-quits after two turns look identical in the logs",
        required_event={"name": "abandonment_reason_judgement", "grain": "session",
                        "fields": ["judge_version", "reason_class", "confidence", "calibration_set_id"],
                        "owner": "quality-judging"}))

    # ---------------------------------------------------------------- A10 --
    queue = _review_queue(corpus, cov, D)
    extras["review_queue"] = queue
    metrics.append(metric(
        "m_review_queue", "Conversations a human should review this week", "A10", "session", "derived",
        {"count": len(queue["items"]), "by_reason": queue["by_reason"]},
        1.0, "the scope uses session fields present on every session; the empty-tool-answer criterion only "
             "fires on agent_kinds that emit tool_call (stated in each item's reason)",
        SESSIONS + " (last 7 days)",
        "unplanned handoff OR abandoned OR empty tool answer OR turns >= tenant p90, ranked by quality_score "
        "within the current judge_version (judged score used for ordering only)",
        "n/a — a queue", breakdowns=["tenant", "intent", "reason"],
        alternatives=["ordering by a judged score is a judged reading; the SCOPE itself is deterministic"],
        breakdown={"top": queue["items"][:25]}))

    # ---------------------------------------------------------------- A11 --
    cap = corpus.kit.capability("failover_rate")
    req = cap.get("required_event") or {"name": "failover", "grain": "step",
                                        "fields": ["from_target", "to_target", "reason", "recovered"],
                                        "owner": "conversation-runtime"}
    n_llm_retries = sum(s["derived"]["n_llm_retries"] for s in all_rows)
    gaps.append(gap(
        "A11", "NOT_MEASURABLE",
        "No failover mechanism exists in the runtime, so no failover event is ever emitted: there is no "
        "primary/secondary path to observe. Nothing in the corpus records a primary model or tool failing "
        "and an alternate serving the user, because that choice is never made. Required event: %s at %s "
        "grain with fields %s, owner %s." % (req["name"], req["grain"], ", ".join(req["fields"]), req["owner"]),
        nearest_proxy="llm_call rows with retry_count > 0 (%d in this corpus)" % n_llm_retries,
        why_the_proxy_misleads="those are QUALITY retries — the same target re-issued after a provider error or "
                               "malformed response, not a switch to an alternate target. Reporting them as "
                               "failover would show a small non-zero rate that reads as 'failover is working' "
                               "when failover does not exist; a chart here would be worse than no chart.",
        required_event={"name": req["name"], "grain": req["grain"], "fields": list(req["fields"]),
                        "owner": req["owner"]}))

    # ---------------------------------------------------------------- C2 --
    # Cardinality refusal is not one of the eleven asks; it is the catalog's
    # denominator trap. It is filed under A02 because that is the cohort
    # drill-down whose per-customer grouping was considered and refused.
    field = "session.custom_dims.customer_ref"
    budget = corpus.kit.cardinality_budget(field)
    distinct = len({(s.get("custom_dims") or {}).get("customer_ref") for s in all_rows
                    if (s.get("custom_dims") or {}).get("customer_ref") is not None})
    if budget is not None and distinct > budget:
        gaps.append(gap(
            "A02", "CARDINALITY_REFUSED",
            "a per-customer breakdown (custom_dims.customer_ref) was considered for the cohort drill-down and "
            "refused: %d distinct values against the catalog's declared cardinality budget of %d for %s. The "
            "field is near-unique per session; grouping by it is a planner error, not a cohort. session_id "
            "is the only declared join key; customer_ref is not one." % (distinct, budget, field),
            nearest_proxy="custom_dims.segment (closed set, budget %s)" % corpus.kit.cardinality_budget("session.custom_dims.segment"),
            why_the_proxy_misleads="segment groups customers into three bands; it answers 'which kind of customer', "
                                   "not 'which customer'"))
    extras["customer_ref_distinct"] = distinct
    return metrics, gaps, extras


def _quality_calibration(corpus: Corpus):
    rub = corpus.kit.rubric_scores()
    pairs = []
    skipped = 0
    for r in rub:
        s = corpus.sessions.get(r.get("session_id"))
        if not s or s.get("quality_score") is None or r.get("human_quality") is None:
            skipped += 1
            continue
        pairs.append((float(r["human_quality"]), float(s["quality_score"]),
                      r.get("judge_version_at_label_time") or s.get("judge_version")))
    if len(pairs) < 10:
        return None, {"note": "fewer than 10 human rubric labels join to sessions; no calibration published"}
    within1 = sum(1 for h, m, _ in pairs if abs(h - m) <= 1.0) / float(len(pairs))
    detail = {"definition": "|human_quality - quality_score| <= 1 on the 1-5 rubric",
              "n": len(pairs), "labels_not_joined": skipped,
              "mae": r4(mean([abs(h - m) for h, m, _ in pairs])),
              "within_half_point": r4(sum(1 for h, m, _ in pairs if abs(h - m) <= 0.5) / float(len(pairs))),
              "by_judge_version": {}}
    for v in sorted({p[2] for p in pairs if p[2]}):
        ps = [p for p in pairs if p[2] == v]
        detail["by_judge_version"][v] = {"n": len(ps),
                                         "within_1": r4(sum(1 for h, m, _ in ps if abs(h - m) <= 1.0) / float(len(ps))),
                                         "mae": r4(mean([abs(h - m) for h, m, _ in ps]))}
    agreement = round(within1, 4)
    if agreement >= 0.995:
        # a perfect agreement is a bug; publish the stricter reading and say so
        agreement = detail["within_half_point"]
        detail["note"] = "within-1 agreement was >= 0.995, which the label set's human disagreement rules out; " \
                         "the half-point agreement is published instead"
        if agreement >= 0.995:
            return None, {"note": "within-1 and half-point agreement are both >= 0.995, which the label set's "
                                  "human disagreement rules out; no calibration is published rather than a "
                                  "suspiciously perfect one",
                          "n": len(pairs), "mae": detail["mae"]}
    versions = sorted(detail["by_judge_version"])
    return {"agreement": agreement, "n": len(pairs),
            "judge_version": "+".join(versions) if versions else "unknown"}, detail


def _review_queue(corpus: Corpus, cov: Coverage, D: int) -> dict:
    start = max(0, D - 7)
    items = []
    by_reason = Counter()
    p90 = {t: percentile([float(s["turns"]) for s in corpus.rows(t)], 0.90) for t in corpus.tenants}
    current_jv = Counter(s.get("judge_version") for s in corpus.sessions.values() if int(s["day"]) >= start).most_common(1)
    jv = current_jv[0][0] if current_jv else None
    for s in corpus.sessions.values():
        if int(s["day"]) < start:
            continue
        reasons = []
        if unplanned_handoff(s):
            reasons.append("unplanned_handoff")
        if abandoned(s):
            reasons.append("abandoned")
        if s["derived"]["n_empty_success"] and cov.covered("tool_call", s):
            reasons.append("empty_tool_answer")
        if float(s["turns"]) >= (p90.get(s["tenant"]) or 1e9):
            reasons.append("turns_at_or_above_p90")
        if not reasons:
            continue
        for r in reasons:
            by_reason[r] += 1
        q = s.get("quality_score")
        items.append({"session_id": s["session_id"], "tenant": s["tenant"], "day": int(s["day"]),
                      "intent": s["intent"], "agent_id": s["agent_id"], "reasons": reasons,
                      "quality_score": q if s.get("judge_version") == jv else None,
                      "judge_version": s.get("judge_version")})
    items.sort(key=lambda i: (i["quality_score"] if i["quality_score"] is not None else 99, -len(i["reasons"]), i["session_id"]))
    return {"items": items, "by_reason": dict(by_reason), "window_days": [start, D - 1], "ranking_judge_version": jv}
