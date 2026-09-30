"""Per-day series and the shared window-scan primitive.

A ``DaySeries`` holds, for each day, the count, sum and sum of squares of a
session-grain (or step-grain) measure. ``best_window`` scans every contiguous
day window and returns the one whose inside mean differs most from the outside
mean, as a z-score. Faults in this corpus start *and end*, so an inside/outside
contrast is more robust than a single before/after split; windows that run to
the last day degrade gracefully to a plain changepoint.

No day, tenant, intent, tool or agent literal lives here.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional


class DaySeries:
    def __init__(self, days: int):
        self.days = days
        self.n = [0] * days
        self.s = [0.0] * days
        self.ss = [0.0] * days

    def add(self, day: int, value: float, weight: int = 1) -> None:
        if 0 <= day < self.days:
            self.n[day] += weight
            self.s[day] += value
            self.ss[day] += value * value

    def add_rate(self, day: int, num: int, den: int) -> None:
        """Proportion series: ``den`` trials of which ``num`` were positive."""
        if 0 <= day < self.days and den:
            self.n[day] += den
            self.s[day] += num
            self.ss[day] += num

    def total(self) -> int:
        return sum(self.n)

    def mean_between(self, a: int, b: int) -> Optional[float]:
        n = sum(self.n[a:b])
        return (sum(self.s[a:b]) / n) if n else None

    def daily_means(self) -> Dict[int, float]:
        return {d: self.s[d] / self.n[d] for d in range(self.days) if self.n[d]}

    def first_day(self) -> Optional[int]:
        for d in range(self.days):
            if self.n[d]:
                return d
        return None

    def last_day(self) -> Optional[int]:
        for d in range(self.days - 1, -1, -1):
            if self.n[d]:
                return d
        return None


class Window:
    def __init__(self, a: int, b: int, inside: float, outside: float, z: float,
                 n_in: int, n_out: int):
        self.a, self.b = a, b                # [a, b) in days
        self.inside, self.outside = inside, outside
        self.z, self.n_in, self.n_out = z, n_in, n_out

    @property
    def from_day(self) -> int:
        return self.a

    @property
    def to_day(self) -> int:
        return self.b - 1

    def __repr__(self) -> str:
        return ("Window(%d..%d inside=%.4f outside=%.4f z=%.1f n_in=%d n_out=%d)"
                % (self.a, self.to_day, self.inside, self.outside, self.z, self.n_in, self.n_out))


def _prefix(xs):
    out = [0.0]
    for x in xs:
        out.append(out[-1] + x)
    return out


def best_window(series: DaySeries, direction: str, min_len: int = 3,
                max_len: Optional[int] = None, min_n_in: int = 30,
                min_n_out: int = 30, min_days_out: int = 3,
                open_ended: bool = True) -> Optional[Window]:
    """The contiguous window whose inside mean is most ``direction`` ("up" or
    "down") of the outside mean, by Welch z-score. Returns None when no window
    with enough support exists.

    ``open_ended``: allow windows that run to the last observed day (a fault that
    has not recovered yet). The outside is then the before-period only.
    """
    D = series.days
    pn, ps, pss = _prefix(series.n), _prefix(series.s), _prefix(series.ss)
    N, S, SS = pn[-1], ps[-1], pss[-1]
    if N < min_n_in + min_n_out:
        return None
    days_with_data = [d for d in range(D) if series.n[d]]
    if not days_with_data:
        return None
    last = days_with_data[-1] + 1
    max_len = max_len or D
    sign = 1.0 if direction == "up" else -1.0
    best: Optional[Window] = None
    for a in range(D):
        if not series.n[a]:
            continue                       # windows open on a day with traffic
        for b in range(a + min_len, min(D, a + max_len) + 1):
            if b < last and not series.n[b - 1]:
                continue                   # ...and close on one
            if b >= last and not open_ended:
                break
            n_in = pn[b] - pn[a]
            n_out = N - n_in
            if n_in < min_n_in or n_out < min_n_out:
                continue
            d_out = len(days_with_data) - sum(1 for d in days_with_data if a <= d < b)
            if d_out < min_days_out:
                continue
            s_in, ss_in = ps[b] - ps[a], pss[b] - pss[a]
            m_in = s_in / n_in
            m_out = (S - s_in) / n_out
            v_in = max(ss_in / n_in - m_in * m_in, 0.0)
            v_out = max((SS - ss_in) / n_out - m_out * m_out, 0.0)
            se = math.sqrt(v_in / n_in + v_out / n_out) if (v_in or v_out) else 0.0
            if se == 0.0:
                z = 0.0 if m_in == m_out else float("inf") * sign * (1 if m_in > m_out else -1)
            else:
                z = sign * (m_in - m_out) / se
            if z <= 0:
                continue
            if best is None or z > best.z:
                best = Window(a, b, m_in, m_out, z, n_in, n_out)
    return best


def elevated_run(series: DaySeries, start: int, threshold: float, direction: str,
                 allow_gap: int = 1) -> int:
    """Last day (inclusive) of the run of days from ``start`` whose daily mean stays
    beyond ``threshold`` in ``direction``; tolerates ``allow_gap`` quiet days."""
    means = series.daily_means()
    last = start
    gap = 0
    for d in range(start, series.days):
        if d not in means:
            continue
        ok = means[d] > threshold if direction == "up" else means[d] < threshold
        if ok:
            last, gap = d, 0
        else:
            gap += 1
            if gap > allow_gap:
                break
    return last


def contrast(series: DaySeries, a: int, b: int) -> Dict[str, Optional[float]]:
    inside = series.mean_between(a, b)
    n_in = sum(series.n[a:b])
    n_out = series.total() - n_in
    s_out = sum(series.s) - sum(series.s[a:b])
    return {"inside": inside, "outside": (s_out / n_out) if n_out else None,
            "n_in": n_in, "n_out": n_out}


def values_between(rows: List[dict], a: int, b: int, key) -> List[float]:
    return [key(r) for r in rows if a <= int(r["day"]) < b and key(r) is not None]
