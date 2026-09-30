"""Emission coverage, computed from the mounted corpus — never copied from prose.

Coverage is a property of a *scope*: a step type is covered for a session when
that session's ``agent_kind`` is one that emits the step type anywhere in the
corpus. Sessions whose kind never emits the step type must leave the
denominator rather than be counted as zero.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List

from .features import Corpus


class Coverage:
    def __init__(self, corpus: Corpus):
        self.corpus = corpus
        self.kinds_emitting: Dict[str, set] = {}
        emitted_types = set()
        for kind, cnt in corpus.step_types_by_kind.items():
            emitted_types.update(t for t, n in cnt.items() if n > 0)
        for t in emitted_types:
            self.kinds_emitting[t] = {k for k, cnt in corpus.step_types_by_kind.items()
                                      if cnt.get(t, 0) > 0}
        self.kind_counts: Dict[str, Counter] = {}
        for s in corpus.sessions.values():
            self.kind_counts.setdefault(s["tenant"], Counter())[s.get("agent_kind")] += 1

    def emitting_kinds(self, step_type: str) -> List[str]:
        return sorted(self.kinds_emitting.get(step_type, set()))

    def excluded_kinds(self, step_type: str) -> List[str]:
        all_kinds = set()
        for c in self.kind_counts.values():
            all_kinds.update(c.keys())
        return sorted(all_kinds - self.kinds_emitting.get(step_type, set()))

    def value(self, step_type: str, tenant: str) -> float:
        """Share of the tenant's sessions whose agent_kind emits ``step_type``."""
        cnt = self.kind_counts.get(tenant) or Counter()
        total = sum(cnt.values())
        if not total:
            return 0.0
        ok = sum(n for k, n in cnt.items() if k in self.kinds_emitting.get(step_type, set()))
        return round(ok / float(total), 4)

    def covered(self, step_type: str, s: dict) -> bool:
        return s.get("agent_kind") in self.kinds_emitting.get(step_type, set())

    def basis(self, step_type: str, tenant: str) -> str:
        exc = self.excluded_kinds(step_type)
        if not exc:
            return ("every agent_kind in this corpus emits %s rows, so every session "
                    "can contribute" % step_type)
        return ("agent_kind %s emits no %s rows anywhere in the corpus; those sessions "
                "are excluded from the denominator rather than counted as zero. Value is "
                "the share of %s sessions on an emitting agent_kind, measured from this "
                "corpus, not assumed." % (", ".join(exc), step_type, tenant))

    def excluded_filter(self, step_type: str) -> List[str]:
        return ["agent_kind = %s" % k for k in self.excluded_kinds(step_type)]

    def summary(self) -> dict:
        return {t: {st: self.value(st, t) for st in sorted(self.kinds_emitting)}
                for t in self.corpus.tenants}
