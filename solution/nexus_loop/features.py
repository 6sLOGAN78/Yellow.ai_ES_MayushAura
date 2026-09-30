"""Session-grain feature table.

One dict per session. Raw session fields are kept untouched (unknown fields
included); everything reduced from ``agent_steps`` lands under ``row["derived"]``
so step measures are aggregated to session grain *before* any outcome math —
the fan-out error the catalog warns about cannot happen downstream.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional

from .io import Kit


def new_derived() -> dict:
    return {
        "n_steps": 0,
        "n_tool_calls": 0,
        "n_tool_ok": 0,
        "n_tool_errors": 0,          # outcome != ok on a tool_call
        "n_tool_timeouts": 0,
        "n_empty_success": 0,        # status 200, outcome ok, result_field_count == 0
        "calls_by_tool": Counter(),
        "errors_by_tool": Counter(),
        "empty_by_tool": Counter(),
        "n_same_tool_retry": 0,      # retry_count>0 on a tool already called this session
        "tool_latencies": [],
        "tool_versions": set(),
        "n_kb_lookups": 0,
        "n_kb_hits": 0,
        "kb_scores": [],
        "n_llm_calls": 0,
        "n_llm_errors": 0,
        "n_llm_retries": 0,
        "n_guardrail_blocks": 0,
        "n_handoff_steps": 0,
        "handoff_by_design_step": None,
        "milestones": [],
    }


class Corpus:
    """The loaded corpus: session table + config timeline + a few indexes."""

    def __init__(self, kit: Kit):
        self.kit = kit
        self.sessions: Dict[str, dict] = {}
        self.changes: List[dict] = kit.config_changes()
        self.tenants: List[str] = []
        self.days: int = 0
        # which agent_kinds ever emitted a step of each type (coverage evidence)
        self.step_types_by_kind: Dict[str, Counter] = defaultdict(Counter)
        self.orphan_steps = 0

    # ------------------------------------------------------------------ load
    def load(self) -> "Corpus":
        for s in self.kit.sessions():
            s["derived"] = new_derived()
            self.sessions[s["session_id"]] = s
        if not self.sessions:
            raise RuntimeError("no sessions in corpus")
        self.tenants = sorted({s["tenant"] for s in self.sessions.values()})
        self.days = max(int(s["day"]) for s in self.sessions.values()) + 1

        for st in self.kit.steps():
            s = self.sessions.get(st["session_id"])
            kind = st.get("agent_kind") or (s or {}).get("agent_kind") or "?"
            self.step_types_by_kind[kind][st.get("step_type")] += 1
            if s is None:
                self.orphan_steps += 1
                continue
            self._fold(s["derived"], st)
        for s in self.sessions.values():
            d = s["derived"]
            d["tool_versions"] = sorted(d["tool_versions"])
        return self

    @staticmethod
    def _fold(d: dict, st: dict) -> None:
        d["n_steps"] += 1
        t = st.get("step_type")
        if t == "tool_call":
            tool = st.get("tool_name") or "?"
            d["n_tool_calls"] += 1
            d["calls_by_tool"][tool] += 1
            if st.get("tool_version"):
                d["tool_versions"].add("%s@%s" % (tool, st["tool_version"]))
            if st.get("duration_ms") is not None:
                d["tool_latencies"].append(st["duration_ms"])
            if st.get("outcome") == "ok":
                d["n_tool_ok"] += 1
                # only an explicit result_field_count == 0 is an empty success.
                # A missing key is unknown, not empty — treating absent as zero
                # would flag a valid response as a silent failure.
                rfc = st.get("result_field_count")
                if st.get("status_code") == 200 and rfc is not None and rfc == 0:
                    d["n_empty_success"] += 1
                    d["empty_by_tool"][tool] += 1
            else:
                d["n_tool_errors"] += 1
                d["errors_by_tool"][tool] += 1
                if st.get("outcome") == "timeout":
                    d["n_tool_timeouts"] += 1
            # a retry of a tool this session already called is the runtime's own
            # signal that the first answer was unusable
            if (st.get("retry_count") or 0) > 0 and d["calls_by_tool"][tool] > 1:
                d["n_same_tool_retry"] += 1
        elif t == "kb_lookup":
            d["n_kb_lookups"] += 1
            if st.get("kb_hit"):
                d["n_kb_hits"] += 1
            if st.get("kb_top_score") is not None:
                d["kb_scores"].append(float(st["kb_top_score"]))
        elif t == "llm_call":
            d["n_llm_calls"] += 1
            if st.get("outcome") != "ok":
                d["n_llm_errors"] += 1
            if (st.get("retry_count") or 0) > 0:
                d["n_llm_retries"] += 1
        elif t == "guardrail":
            if st.get("outcome") == "blocked":
                d["n_guardrail_blocks"] += 1
        elif t == "handoff":
            d["n_handoff_steps"] += 1
            if st.get("handoff_by_design") is not None:
                d["handoff_by_design_step"] = bool(st["handoff_by_design"])
        elif t == "turn":
            if st.get("milestone"):
                d["milestones"].append(st["milestone"])

    # --------------------------------------------------------------- helpers
    def rows(self, tenant: Optional[str] = None) -> Iterable[dict]:
        if tenant is None:
            return self.sessions.values()
        return (s for s in self.sessions.values() if s["tenant"] == tenant)

    def changes_for(self, tenant: str, kind: Optional[str] = None,
                    target: Optional[str] = None) -> List[dict]:
        out = []
        for c in self.changes:
            if c.get("tenant") not in (tenant, "*"):
                continue
            if kind and c.get("kind") != kind:
                continue
            if target and c.get("target") != target:
                continue
            out.append(c)
        return out

    def nearest_change(self, tenant: str, day: int, kinds, target: Optional[str] = None,
                       before: int = 3, after: int = 2) -> Optional[dict]:
        """The config change of one of ``kinds`` closest to ``day`` inside
        [day-before, day+after]; None when nothing aligns. Attribution is
        evidence, never a gate on whether a regression is reported.

        On an exact tie the earlier change in timeline order wins. Callers that
        need mechanism-awareness (e.g. prompt versus model on the same day)
        should choose by the dimension that actually changed in the data, not
        rely on this tie-break."""
        best = None
        for c in self.changes:
            if c.get("tenant") not in (tenant, "*") or c.get("kind") not in kinds:
                continue
            if target is not None and c.get("target") != target:
                continue
            if not (day - before <= c["day"] <= day + after):
                continue
            if best is None or abs(c["day"] - day) < abs(best["day"] - day):
                best = c
        return best


# ----------------------------------------------------------------- outcomes

def resolved(s: dict) -> bool:
    return s.get("session_end") == "resolved"


def by_design_handoff(s: dict) -> bool:
    """Session field when present, else the step-grain flag folded at load.
    A null in both places is treated as NOT by-design (conservative: it counts
    as an unplanned handoff), matching the previous behaviour for a missing
    session field."""
    v = s.get("handoff_by_design")
    if v is None:
        v = (s.get("derived") or {}).get("handoff_by_design_step")
    return bool(v)


def milestones_of(s: dict) -> list:
    """Session milestone rollup when present, else the list folded from turn
    steps at load time."""
    ms = s.get("milestones_reached")
    if ms is None:
        ms = (s.get("derived") or {}).get("milestones") or []
    return ms


def contained(s: dict) -> bool:
    return resolved(s) or (s.get("session_end") == "handoff" and by_design_handoff(s))


def unplanned_handoff(s: dict) -> bool:
    return s.get("session_end") == "handoff" and not by_design_handoff(s)


def abandoned(s: dict) -> bool:
    return s.get("session_end") == "abandoned"


def median(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(xs)
    n = len(ys)
    mid = n // 2
    return float(ys[mid]) if n % 2 else (ys[mid - 1] + ys[mid]) / 2.0


def percentile(xs: List[float], q: float) -> Optional[float]:
    """Nearest-rank percentile, q in [0,1]."""
    if not xs:
        return None
    ys = sorted(xs)
    k = max(0, min(len(ys) - 1, int(round(q * (len(ys) - 1)))))
    return float(ys[k])


def mean(xs: List[float]) -> Optional[float]:
    return (sum(xs) / float(len(xs))) if xs else None


def rate(num: int, den: int) -> Optional[float]:
    return (num / float(den)) if den else None
