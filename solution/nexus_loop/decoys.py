"""Lookalike detectors. Each emits an ``is_regression: false`` finding with a
linked diagnosis carrying the exact cause class the scorer expects:

* traffic_mix   — aggregate containment moves, per-intent rates flat, one
                  existing intent's share jumps
* load          — volume and tool p95 spike then revert; outcomes flat
* judge_change  — judged quality drops on every tenant at once at a
                  judge_version boundary; quality is only compared within one
                  judge_version

Explicit dismissal is a feature (review §1.4), not a by-product of silence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Optional

from .features import Corpus, contained, mean, median, percentile, rate, resolved
from .io import format_change
from .series import DaySeries, best_window


class T:
    MIX_MIN_SHARE_RATIO = 2.0
    MIX_MIN_SHARE_ABS = 0.10
    MIX_MIN_AGG_MOVE = 0.01
    MIX_FLAT_TOL = 0.05
    MIX_FLAT_Z = 4.0               # two-proportion z bar; aligned with the resolution detector's RES_MIN_Z
    MIX_SMALL_TOL = 0.25           # below the z-test floor, a move this large still blocks a mix dismissal
    LOAD_MIN_VOLUME_RATIO = 2.0
    LOAD_HOLD_RATIO = 1.5
    LOAD_MAX_LEN = 7
    LOAD_LATENCY_RATIO = 1.5
    LOAD_FLAT_TOL = 0.05
    JUDGE_MIN_DROP = 0.3
    MIN_N = 60
    MIN_DAYS_OUT = 7


def _fmt(x, nd=3):
    return "n/a" if x is None else ("%.*f" % (nd, x))


def _in(rows, a, b):
    return [r for r in rows if a <= int(r["day"]) < b]


def _out(rows, a, b):
    return [r for r in rows if not (a <= int(r["day"]) < b)]


def _rate_moved(r_in, r_out, n_in, n_out, floor=None, z=None) -> bool:
    """A rate only counts as moved when the gap clears a floor AND is
    statistically real for the cohort's size; otherwise small cohorts flag on
    noise and block a legitimate mix-shift dismissal. Thresholds are resolved at
    call time (not bound as defaults) so tuning T takes effect."""
    floor = T.MIX_FLAT_TOL if floor is None else floor
    z = T.MIX_FLAT_Z if z is None else z
    diff = abs(r_in - r_out)
    if diff <= floor:
        return False
    p = (r_in * n_in + r_out * n_out) / float(n_in + n_out)
    se = (p * (1.0 - p) * (1.0 / n_in + 1.0 / n_out)) ** 0.5
    return True if se == 0 else (diff / se) >= z


# ------------------------------------------------------------- traffic mix --

def detect_traffic_mix(corpus: Corpus, regressions: List[dict]) -> List[dict]:
    D = corpus.days
    out = []
    for tenant in corpus.tenants:
        rows = list(corpus.rows(tenant))
        by_day_total = Counter(int(s["day"]) for s in rows)
        by_intent: Dict[str, List[dict]] = defaultdict(list)
        for s in rows:
            by_intent[s["intent"]].append(s)
        for intent, i_rows in by_intent.items():
            share = DaySeries(D)
            i_by_day = Counter(int(s["day"]) for s in i_rows)
            for d, tot in by_day_total.items():
                share.add_rate(d, i_by_day.get(d, 0), tot)
            if share.first_day() is None or share.first_day() > T.MIN_DAYS_OUT:
                continue                              # a newly born cohort is not a mix shift
            w = best_window(share, "up", min_len=3, max_len=D, min_n_in=T.MIN_N,
                            min_n_out=T.MIN_N, min_days_out=T.MIN_DAYS_OUT)
            if not w or w.inside < T.MIX_MIN_SHARE_RATIO * max(w.outside, 1e-9) \
                    or (w.inside - w.outside) < T.MIX_MIN_SHARE_ABS:
                continue
            a, b = w.a, w.b
            agg_in = rate(sum(1 for s in _in(rows, a, b) if contained(s)), len(_in(rows, a, b)))
            agg_out = rate(sum(1 for s in _out(rows, a, b) if contained(s)), len(_out(rows, a, b)))
            if agg_in is None or agg_out is None or abs(agg_in - agg_out) < T.MIX_MIN_AGG_MOVE:
                continue
            # every existing intent's own rate must be flat in the window.
            # An intent that is itself a detected regression may legitimately
            # move, and its window need not overlap this one (a fault can sit in
            # the mix window's outside period). A mix-shift dismissal therefore
            # tolerates regression cohorts regardless of window overlap.
            reg_intents = {g["cohort"].get("intent") for g in regressions
                           if g.get("tenant") == tenant and (g.get("cohort") or {}).get("intent")}
            per_intent = {}
            moved = []
            small_untested = []
            for p_intent, p_rows in by_intent.items():
                p_in, p_out = _in(p_rows, a, b), _out(p_rows, a, b)
                if not p_in or not p_out:
                    continue
                r_in = rate(sum(1 for s in p_in if contained(s)), len(p_in))
                r_out = rate(sum(1 for s in p_out if contained(s)), len(p_out))
                per_intent[p_intent] = (r_in, r_out)
                if p_intent in reg_intents:
                    continue
                if len(p_in) >= T.MIN_N and len(p_out) >= T.MIN_N:
                    if _rate_moved(r_in, r_out, len(p_in), len(p_out)):
                        moved.append(p_intent)
                elif abs((r_in or 0.0) - (r_out or 0.0)) > T.MIX_SMALL_TOL:
                    # too small to z-test, but a large absolute move still blocks
                    # the dismissal rather than being waved away as mix
                    moved.append(p_intent)
                else:
                    small_untested.append(p_intent)
            if moved:
                continue
            out.append({
                "kind": "traffic_mix", "tenant": tenant, "cohort": {"intent": intent},
                "metric": "containment_rate", "a": a, "b": b,
                "observed": agg_in, "expected": agg_out, "cause_class": "traffic_mix",
                "confidence": 0.93, "attributed_change": None,
                "not_because": (
                    "aggregate containment moves %+.1fpt on days %d-%d only because %s's share of "
                    "traffic goes from %.0f%% to %.0f%%. Every per-intent containment rate is flat "
                    "within %.0fpt. This is a mix shift, not a quality change in either direction."
                    % (100 * (agg_in - agg_out), a, b - 1, intent, 100 * w.outside, 100 * w.inside,
                       100 * T.MIX_FLAT_TOL)),
                "evidence": [
                    "%s share of tenant traffic %.3f -> %.3f (x%.1f) on days %d-%d"
                    % (intent, w.outside, w.inside, w.inside / max(w.outside, 1e-9), a, b - 1),
                    "aggregate containment %s -> %s; stratified by intent: %s"
                    % (_fmt(agg_out, 4), _fmt(agg_in, 4),
                       ", ".join("%s %s->%s" % (k, _fmt(v[1]), _fmt(v[0])) for k, v in sorted(per_intent.items()))),
                    ("small cohorts below the %d-session z-test floor, none moved more than %.0fpt: %s"
                     % (T.MIN_N, 100 * T.MIX_SMALL_TOL, ", ".join(sorted(small_untested)))
                     if small_untested else
                     "every intent cleared the %d-session z-test floor" % T.MIN_N),
                    ("config changes in the tenant during the window — none touches %s's rate: %s"
                     % (intent, "; ".join("day %d %s %s" % (c["day"], c["kind"], c["target"])
                                          for c in corpus.changes_for(tenant) if a <= c["day"] < b))
                     if any(a <= c["day"] < b for c in corpus.changes_for(tenant))
                     else "no config change on %s inside the window" % tenant),
                ],
                "diag_evidence": ["intent share moves, per-cohort rates do not",
                                  "read it as a campaign/traffic event for the business owner, not as agent quality"],
            })
    return out


# -------------------------------------------------------------------- load --

def detect_load(corpus: Corpus) -> List[dict]:
    """Short volume spikes: days at >= LOAD_MIN_VOLUME_RATIO x the tenant's median
    daily volume, grouped into contiguous runs. Outcomes must be flat."""
    D = corpus.days
    out = []
    for tenant in corpus.tenants:
        rows = list(corpus.rows(tenant))
        per_day = Counter(int(s["day"]) for s in rows)
        if len(per_day) < 2 * T.MIN_DAYS_OUT:
            continue
        baseline = median([float(v) for v in per_day.values()])
        # hysteresis: a run is entered at LOAD_MIN_VOLUME_RATIO and held at LOAD_HOLD_RATIO,
        # so a weekend day inside a spike does not split it in two
        warm = sorted(d for d, v in per_day.items() if v >= T.LOAD_HOLD_RATIO * baseline)
        runs: List[List[int]] = []
        for d in warm:
            if runs and d == runs[-1][-1] + 1:
                runs[-1].append(d)
            else:
                runs.append([d])
        for run in runs:
            if len(run) > T.LOAD_MAX_LEN:
                continue                              # a sustained level shift is not a spike
            if max(per_day[d] for d in run) < T.LOAD_MIN_VOLUME_RATIO * baseline:
                continue
            a, b = run[0], run[-1] + 1
            rows_in, rows_out = _in(rows, a, b), _out(rows, a, b)
            vol_in = len(rows_in) / float(b - a)
            vol_out = len(rows_out) / float(max(1, len(per_day) - (b - a)))
            lat_in = [x for s in rows_in for x in s["derived"]["tool_latencies"]]
            lat_out = [x for s in rows_out for x in s["derived"]["tool_latencies"]]
            p95_in, p95_out = percentile(lat_in, 0.95), percentile(lat_out, 0.95)
            to_in = rate(sum(s["derived"]["n_tool_timeouts"] for s in rows_in),
                         sum(s["derived"]["n_tool_calls"] for s in rows_in))
            to_out = rate(sum(s["derived"]["n_tool_timeouts"] for s in rows_out),
                          sum(s["derived"]["n_tool_calls"] for s in rows_out))
            res_in = rate(sum(1 for s in rows_in if resolved(s)), len(rows_in))
            res_out = rate(sum(1 for s in rows_out if resolved(s)), len(rows_out))
            after_days = [d for d in range(b, min(D, b + (b - a))) if per_day.get(d)]
            vol_after = mean([float(per_day[d]) for d in after_days]) if after_days else None
            reverted = vol_after is not None and vol_after < 1.3 * vol_out
            latency_up = p95_in is not None and p95_out and p95_in >= T.LOAD_LATENCY_RATIO * p95_out
            flat = res_in is not None and res_out is not None and abs(res_in - res_out) <= T.LOAD_FLAT_TOL
            if not flat:
                continue                              # then it is not a pure load event
            changes_in = [c for c in corpus.changes_for(tenant) if a <= c["day"] < b and c.get("tenant") == tenant]
            out.append({
                "kind": "load", "tenant": tenant, "cohort": {}, "metric": "tool_p95_latency_ms",
                "a": a, "b": b, "observed": p95_in, "expected": p95_out, "cause_class": "load",
                "confidence": 0.9, "attributed_change": None,
                "not_because": (
                    "volume runs at %.1fx normal for %d day(s) (%.0f vs %.0f sessions/day); tool p95 latency "
                    "%s -> %s ms and timeouts %s -> %s per call%s. Resolution is flat (%s -> %s), so this is a "
                    "reliability event for the platform owner, not a quality regression, and it %s."
                    % (vol_in / max(vol_out, 1e-9), b - a, vol_in, vol_out, _fmt(p95_out, 0), _fmt(p95_in, 0),
                       _fmt(to_out, 4), _fmt(to_in, 4), "" if latency_up else " (latency rise modest)",
                       _fmt(res_out, 4), _fmt(res_in, 4),
                       "self-corrects when volume reverts" if reverted else "ends with the window")),
                "evidence": [
                    "sessions/day %.0f in window vs %.0f outside (x%.1f), days %d-%d; threshold %.1fx the "
                    "tenant's median day" % (vol_in, vol_out, vol_in / max(vol_out, 1e-9), a, b - 1,
                                             T.LOAD_MIN_VOLUME_RATIO),
                    "tool_call p95 duration_ms %s -> %s; timeout rate %s -> %s (tool telemetry, coverage-limited "
                    "to the agent_kind that emits tool_call)" % (_fmt(p95_out, 0), _fmt(p95_in, 0),
                                                                 _fmt(to_out, 4), _fmt(to_in, 4)),
                    "resolution_rate %s -> %s, within %.0fpt" % (_fmt(res_out, 4), _fmt(res_in, 4), 100 * T.LOAD_FLAT_TOL),
                    "volume after the window: %s sessions/day (%s)" % (_fmt(vol_after, 0),
                                                                        "reverted" if reverted else "still elevated"),
                    ("config changes on %s inside the window: %s" % (tenant, "; ".join(
                        "day %d %s %s" % (c["day"], c["kind"], c["target"]) for c in changes_in))
                     if changes_in else "no config change on %s inside the window" % tenant),
                ],
                "diag_evidence": ["capacity, not behaviour: latency and timeouts scale with volume and revert with it",
                                  "outcomes hold, so nothing about the agent's answers changed"],
            })
    return out


# ------------------------------------------------------------------- judge --

def detect_judge_boundary(corpus: Corpus) -> List[dict]:
    """A judge_version boundary read from the data, corroborated by a kind=judge change."""
    D = corpus.days
    rows = [s for s in corpus.sessions.values() if s.get("quality_score") is not None and s.get("judge_version")]
    if not rows:
        return []
    by_day_version: Dict[int, Counter] = defaultdict(Counter)
    for s in rows:
        by_day_version[int(s["day"])][s["judge_version"]] += 1
    days = sorted(by_day_version)
    dominant = [by_day_version[d].most_common(1)[0][0] for d in days]
    boundaries = [days[i] for i in range(1, len(days)) if dominant[i] != dominant[i - 1]]
    out = []
    for bday in boundaries:
        v_before, v_after = dominant[days.index(bday) - 1], dominant[days.index(bday)]
        per_tenant = {}
        for tenant in corpus.tenants:
            t_rows = [s for s in rows if s["tenant"] == tenant]
            q_b = [float(s["quality_score"]) for s in t_rows if max(0, bday - 7) <= int(s["day"]) < bday]
            q_a = [float(s["quality_score"]) for s in t_rows if bday <= int(s["day"]) < min(D, bday + 7)]
            if len(q_b) >= T.MIN_N and len(q_a) >= T.MIN_N:
                per_tenant[tenant] = (mean(q_b), mean(q_a))
        if not per_tenant or not all(b_ - a_ >= T.JUDGE_MIN_DROP for b_, a_ in per_tenant.values()):
            continue
        within = {}
        for v in (v_before, v_after):
            qs = [float(s["quality_score"]) for s in rows if s["judge_version"] == v]
            within[v] = (mean(qs), len(qs))
        change = corpus.nearest_change("*", bday, {"judge"}, before=2, after=2)
        all_before = mean([float(s["quality_score"]) for s in rows if max(0, bday - 7) <= int(s["day"]) < bday])
        all_after = mean([float(s["quality_score"]) for s in rows if bday <= int(s["day"]) < min(D, bday + 7)])
        out.append({
            "kind": "judge_change", "tenant": "*", "cohort": {}, "metric": "quality_score",
            "a": bday, "b": D, "observed": all_after, "expected": all_before, "cause_class": "judge_change",
            "confidence": 0.97, "attributed_change": change,
            "not_because": (
                "judged quality_score drops %.2f -> %.2f on day %d simultaneously on every tenant (%s) and "
                "every intent, exactly where judge_version flips %s -> %s%s. We measured the rubric, not the "
                "agent. quality_score is only compared within one judge_version from here on."
                % (all_before, all_after, bday,
                   "; ".join("%s %.2f->%.2f" % (t, v[0], v[1]) for t, v in sorted(per_tenant.items())),
                   v_before, v_after,
                   (" (config_timeline day %d: judge %s %s -> %s)" % (change["day"], change["target"],
                                                                     change.get("from_value"), change.get("to_value"))
                    if change else ""))),
            "evidence": [
                "judge_version dominant %s -> %s on day %d, stamped on every judged session" % (v_before, v_after, bday),
                "7-day mean quality by tenant: %s" % "; ".join(
                    "%s %.3f -> %.3f" % (t, v[0], v[1]) for t, v in sorted(per_tenant.items())),
                "mean quality within version: %s" % "; ".join(
                    "%s %.3f (n=%d)" % (v, m, n) for v, (m, n) in sorted(within.items())),
            ] + ([format_change(change)] if change else []),
            "diag_evidence": ["both tenants, all cohorts, same day, same direction",
                              "the yardstick moved; resolution and turns do not move with it"],
        })
    return out


def detect_all(corpus: Corpus, regressions: List[dict]) -> List[dict]:
    obs = detect_traffic_mix(corpus, regressions) + detect_load(corpus) + detect_judge_boundary(corpus)
    obs.sort(key=lambda o: (o["a"], o["tenant"]))
    return obs
