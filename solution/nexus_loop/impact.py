"""Impact blocks: every number computed from the cohort rows, with one
derivation string that lets a reader check it. Cost is the sum of recorded
``cost_usd`` in the cohort or null — never an invented handoff price.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .coverage import Coverage
from .features import (Corpus, abandoned, mean, median, rate, resolved, unplanned_handoff)


def _tenant_window_n(corpus: Corpus, tenant: str, a: int, b: int) -> int:
    return sum(1 for s in corpus.rows(tenant) if a <= int(s["day"]) < b)


def _cost(rows: List[dict], cov: Coverage):
    priced = [float(s["cost_usd"]) for s in rows if s.get("cost_usd") is not None]
    if not priced:
        return None, 0
    return round(sum(priced), 2), len(priced)


def impact_for(obs: dict, corpus: Corpus, cov: Coverage) -> Dict:
    rows = obs["rows_in"]
    a, b = obs["a"], obs["b"]
    n = len(rows)
    tenant_n = _tenant_window_n(corpus, obs["tenant"], a, b)
    share = round(n / float(tenant_n), 4) if tenant_n else 0.0
    days = b - a
    cost, priced = _cost(rows, cov)
    handoffs = sum(1 for s in rows if unplanned_handoff(s))
    abandons = sum(1 for s in rows if abandoned(s))
    if obs["metric"] == "resolution_rate":
        observed, expected = obs["observed"] or 0.0, obs["expected"] or 0.0
        lost = max(0, int(round((expected - observed) * n)))
        downstream = {"would_have_resolved_at_baseline": lost,
                      "unplanned_handoffs": handoffs, "abandoned": abandons}
        derivation = (
            "%d sessions matched the cohort on days %d-%d (%d days), %.1f%% of %s's %d sessions in the window. "
            "Observed resolution %.4f against a baseline of %.4f (%s), so %d conversations that would have "
            "resolved did not. %d ended in a handoff the agent was not built to make; %d were abandoned. "
            "cost_usd is the sum of recorded cost_usd over the %d cohort sessions that carry it%s; no human "
            "handling price is assumed."
            % (n, a, b - 1, days, 100 * share, obs["tenant"], tenant_n, observed, expected,
               obs["baseline_note"], lost, handoffs, abandons, priced,
               "" if priced == n else " (%d sessions without recorded cost are excluded)" % (n - priced)))
    else:  # turns_to_resolve
        m = obs["mechanism"]
        res_rows = [s for s in rows if resolved(s)]
        extra_turns = (m["mean_in"] - m["mean_base"]) * len(res_rows) if m["mean_in"] and m["mean_base"] else 0.0
        extra_cost = None
        if m["cost_in"] is not None and m["cost_base"] is not None:
            extra_cost = round((m["cost_in"] - m["cost_base"]) * priced, 2)
        downstream = {"extra_turns": int(round(extra_turns)),
                      "resolved_conversations_slowed": len(res_rows),
                      "excess_cost_usd": extra_cost,
                      "unplanned_handoffs": handoffs, "abandoned": abandons}
        derivation = (
            "%d sessions on %s on days %d-%d (%d days), %.1f%% of %s's %d sessions in the window. Median turns "
            "to resolve %s vs %s at baseline (%s); mean %.2f vs %.2f, so the %d resolved conversations took about "
            "%d extra turns in total. Mean recorded cost per session %s vs %s USD, i.e. about %s USD of excess "
            "spend over the window. cost_usd is the sum of recorded cost_usd over the %d cohort sessions that "
            "carry it. Resolution %s vs %s: outcomes are flat, the damage is effort and cost."
            % (n, obs["cohort"].get("agent_id"), a, b - 1, days, 100 * share, obs["tenant"], tenant_n,
               _f(m["median_in"], 1), _f(m["median_base"], 1), obs["baseline_note"], m["mean_in"] or 0, m["mean_base"] or 0,
               len(res_rows), int(round(extra_turns)), _f(m["cost_in"], 5), _f(m["cost_base"], 5),
               _f(extra_cost, 2), priced, _f(m["res_in"], 4), _f(m["res_base"], 4)))
    return {
        "conversations_affected": n,
        "share_of_traffic": share,
        "downstream": downstream,
        "cost_usd": cost,
        "days_running": days,
        "derivation": derivation,
    }


def _f(x, nd):
    return "n/a" if x is None else "%.*f" % (nd, x)


def severity_for(obs: dict, impact: Dict) -> str:
    if obs["metric"] == "resolution_rate":
        deficit = (obs["expected"] or 0) - (obs["observed"] or 0)
        if deficit >= 0.30:
            return "critical"
        if deficit >= 0.05 or impact["share_of_traffic"] >= 0.20:
            return "high"
        return "medium"
    ratio = obs["mechanism"].get("ratio") or 1.0
    if ratio >= 1.4:
        return "high"
    if ratio >= 1.2:
        return "medium"
    return "low"


AUDIENCE = {
    "kb.gap": ["agent_builder", "business_owner"],
    "tool.contract_break": ["agent_builder", "platform_owner"],
    "tool.outage": ["platform_owner"],
    "prompt.regression": ["agent_builder"],
    "model.change": ["agent_builder", "platform_owner"],
    "routing.error": ["platform_owner"],
    "unknown": ["agent_builder", "platform_owner"],
}


def if_nothing_changes(obs: dict, impact: Dict) -> str:
    d = impact["downstream"]
    days = max(1, impact["days_running"])
    if obs["metric"] == "resolution_rate":
        per_day = d["would_have_resolved_at_baseline"] / float(days)
        hand_share = rate(d["unplanned_handoffs"], impact["conversations_affected"]) or 0.0
        extra = ""
        if obs["kind"] == "tool_contract":
            extra = " The tool reports itself healthy throughout, so nothing else will surface this."
        elif obs.get("is_new"):
            extra = " The cohort is new, so these are first impressions of it."
        return ("At the observed rate about %.0f conversations a day in this cohort fail that would have resolved, "
                "and %.0f%% of the cohort reaches a human who was not meant to be involved.%s"
                % (per_day, 100 * hand_share, extra))
    per_day_turns = d["extra_turns"] / float(days)
    cost_txt = (" and about %.2f USD a day of excess model spend" % (d["excess_cost_usd"] / float(days))
                if d.get("excess_cost_usd") is not None else "")
    return ("Every day this runs costs roughly %.0f extra user turns%s on this agent, with no change in how "
            "often it resolves — the users pay in time, the tenant pays in tokens, and no outcome alert will "
            "ever fire." % (per_day_turns, cost_txt))
