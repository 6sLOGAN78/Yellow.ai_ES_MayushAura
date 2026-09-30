"""Diagnoses and decision-shaped prescriptions.

A diagnosis names the cause class, its confidence, the blamed config change
(or null) and the evidence. A prescription is what a human is asked to
approve, with the honest case against approving it (review §1.5).
"""
from __future__ import annotations

from typing import Dict, List, Optional

CAUSE_CLASSES = {"kb.gap", "tool.contract_break", "tool.outage", "prompt.regression", "model.change",
                 "routing.error", "traffic_mix", "load", "judge_change", "unknown"}

# direct fix per cause class: (change_type, autonomy rung)
DIRECT_FIX = {
    "kb.gap": ("kb.add", "L3"),
    "tool.contract_break": ("tool.validate", "L2"),
    "prompt.regression": ("prompt.edit", "L2"),
    "model.change": ("revert", "L1"),
}
# a less direct mitigation, offered as the alternative in the decision text
ALTERNATIVE = {"kb.gap": "kb.synonym", "tool.contract_break": "tool.fallback", "prompt.regression": "revert"}
RECOVERY_SHARE = 0.9   # predicted: nine tenths of the deficit closes; the rest is genuine edge cases


def diagnosis(diag_id: str, finding_id: str, obs: dict) -> dict:
    cause = obs["cause_class"] if obs["cause_class"] in CAUSE_CLASSES else "unknown"
    ac = obs.get("attributed_change")
    return {
        "id": diag_id,
        "finding_id": finding_id,
        "cause_class": cause,
        "confidence": round(float(obs.get("confidence", 0.5)), 2),
        "attributed_change": ({"kind": ac["kind"], "day": int(ac["day"]), "target": ac.get("target"),
                               "from": ac.get("from_value"), "to": ac.get("to_value")} if ac else None),
        "evidence": list(obs.get("diag_evidence") or []),
    }


def prescription(p_id: str, diag_id: str, obs: dict, standard: Optional[dict]) -> Optional[dict]:
    cause = obs["cause_class"]
    if cause not in DIRECT_FIX:
        return None
    change_type, rung = DIRECT_FIX[cause]
    cohort = obs["cohort"]
    a, b = obs["a"], obs["b"]
    if cause == "kb.gap":
        target = cohort.get("intent", "")
        observed, expected = obs["observed"], obs["expected"]
        to = round(observed + RECOVERY_SHARE * (expected - observed), 4)
        peers = ", ".join(obs.get("peer_intents") or []) or "the peer intents"
        pd = {"metric": "resolution_rate", "from": round(observed, 4), "to": to}
        description = ("Add retrievable KB content for '%s' so kb_lookup returns a confident document: the "
                       "questions users actually asked in the window are the content brief. kb_hit is currently "
                       "%s in the cohort against %s on %s." % (
                           target, _f(obs["mechanism"]["kb"]["hit_rate"]),
                           _f((obs["mechanism"].get("kb_peer") or {}).get("hit_rate")), peers))
        asking = ("Publish knowledge-base articles covering '%s' questions to the %s knowledge base, so the agent "
                  "can ground its answers instead of falling through to a generic reply." % (target, obs["tenant"]))
        risk = ("Low. If retrieval is not the cause, we have added correct content the agent did not have and "
                "resolution does not move; nothing existing changes. The replay golden set (%d known-good "
                "sessions) guards against a regression on other intents." % len(obs.get("golden_set") or []))
        no_ship = ("The replay shows any golden-set regression, or the questions in this cohort turn out to need a "
                   "business decision (pricing, eligibility) nobody has made — then the right fix is a scripted "
                   "handoff, not an article. Alternative if content cannot ship: %s." % ALTERNATIVE[cause])
    elif cause == "tool.contract_break":
        tool = cohort.get("tool") or obs["mechanism"].get("tool")
        target = tool
        observed, expected = obs["observed"], obs["expected"]
        to = round(observed + RECOVERY_SHARE * (expected - observed), 4)
        pd = {"metric": "resolution_rate", "from": round(observed, 4), "to": to}
        description = ("Validate the %s response before handing it to the model: a 200 with result_field_count = 0 "
                       "is treated as upstream.malformed_response, the existing fallback path is taken, and the "
                       "empty answer is counted as a tool failure. %.1f%% of 'ok' calls were empty in the window."
                       % (tool, 100 * obs["mechanism"]["empty_success_rate_in"]))
        asking = ("Make %s reject an empty HTTP 200 as a failure and take the existing fallback path, instead of "
                  "letting the agent apologise with nothing to say." % tool)
        risk = ("Moderate. If some records legitimately return an empty payload (a brand-new order, a cancelled one), "
                "we would start treating a valid state as an error and route those users to a human unnecessarily. "
                "Confirm with the owning team that an empty 200 is never a valid state.")
        no_ship = ("The upstream team confirms empty 200s are a valid state, or the empty-payload rate has already "
                   "fallen after their own fix — then we would be patching a contract about to change again. "
                   "Alternative: %s (route empties to a fallback copy path without changing failure accounting)."
                   % ALTERNATIVE[cause])
    else:  # prompt.regression / model.change → edit or revert on the agent
        agent = cohort.get("agent_id", "")
        target = agent
        m = obs["mechanism"]
        frm, base = m["median_in"], m["median_base"]
        to = round(base + (1 - RECOVERY_SHARE) * (frm - base), 2)
        pd = {"metric": "median_turns", "from": float(frm), "to": to}
        ac = obs.get("attributed_change") or {}
        description = ("Edit the %s prompt (%s -> %s, '%s') to remove the extra confirmation step: median turns to "
                       "resolve went %s -> %s and cost per session %s -> %s USD while resolution stayed at %s -> %s."
                       % (agent, ac.get("from_value", "?"), ac.get("to_value", "?"), ac.get("note", ""),
                          _f(base, 1), _f(frm, 1), _f(m["cost_base"], 5), _f(m["cost_in"], 5),
                          _f(m["res_base"], 4), _f(m["res_in"], 4)))
        asking = ("Change the live prompt on %s so it confirms once, not on every turn — keeping the safety wording "
                  "that the %s change introduced." % (agent, ac.get("kind", "prompt")))
        risk = ("Moderate. The confirmation wording was added for safety; if the extra turns are the price of a "
                "compliance requirement, removing them trades cost for risk and that is a business owner's call, "
                "not ours. Outcomes are flat today, so there is no resolution to lose, only the extra confirmations.")
        no_ship = ("The prompt owner confirms the confirmations are a regulatory requirement, or the replay shows "
                   "resolution falling as turns fall. Alternative: %s to the previous prompt version while a "
                   "shorter wording is drafted." % ALTERNATIVE.get(cause, "revert"))
    return {
        "id": p_id,
        "diagnosis_id": diag_id,
        "change_type": change_type,
        "target": target,
        "description": description,
        "autonomy_rung": rung,
        "predicted_delta": pd,
        "decision": {
            "asking_approval_for": asking,
            "risk_if_diagnosis_wrong": risk,
            "would_not_ship_if": no_ship,
        },
        "cohort": dict(cohort, from_day=a, to_day=b - 1),
        "standard_ref": (standard or {}).get("metric"),
    }


def _f(x, nd=3):
    return "n/a" if x is None else "%.*f" % (nd, x)
