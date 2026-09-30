"""Kit ingest.

Ingest rule (adversarial review §3.3): validate the *required known* keys per
file, preserve every unknown field, and fail clearly only when a field the
pipeline needs is missing. Nothing is quarantined for being absent from the
catalog — the catalog is a planning contract, not an exhaustive column list.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import tempfile
from typing import Dict, Iterator, List, Optional


class KitError(RuntimeError):
    pass


REQUIRED_SESSION_KEYS = ("session_id", "tenant", "day", "intent", "agent_id",
                         "agent_kind", "session_end", "turns")
REQUIRED_STEP_KEYS = ("session_id", "tenant", "day", "step_type")


def stream_jsonl(path: str) -> Iterator[dict]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_jsonl(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    return list(stream_jsonl(path))


def read_csv(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json_atomic(path: str, obj, *, indent: int = 2, sort_keys: bool = False,
                      allow_nan: bool = False) -> None:
    """Write JSON so a reader never sees a partial file.

    A unique temp file in the same directory (so os.replace stays on one
    filesystem), an fsync before the replace, and a cleanup on any failure.
    A fixed temp name would let two writers collide on the same path.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=indent, sort_keys=sort_keys, allow_nan=allow_nan)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def check_required(row: dict, keys, what: str) -> None:
    missing = [k for k in keys if k not in row]
    if missing:
        raise KitError("%s row is missing required field(s) %s — cannot proceed. "
                       "Row keys: %s" % (what, missing, sorted(row.keys())))


class Kit:
    """Paths and small static files of one kit directory."""

    def __init__(self, root: str, corpus_dir: Optional[str] = None):
        self.root = root
        self.corpus_dir = corpus_dir or os.path.join(root, "corpus")
        if not os.path.isdir(self.corpus_dir):
            raise KitError("corpus directory not found: %s" % self.corpus_dir)
        self.catalog = read_json(os.path.join(root, "catalog.json"), default={}) or {}
        self.manifest = read_json(os.path.join(root, "manifest.json"), default={}) or {}
        self.corpus_variant = str(self.manifest.get("corpus_variant")
                                  or self.catalog.get("corpus_variant") or "unknown")

    # --- corpus files -----------------------------------------------------
    def _corpus_file(self, name: str) -> str:
        p = os.path.join(self.corpus_dir, name)
        if not os.path.exists(p):
            # the shared config timeline lives next to the full corpus when a
            # sample directory does not carry its own copy
            alt = os.path.join(self.root, "corpus", name)
            if os.path.exists(alt):
                return alt
            raise KitError("required corpus file not found: %s" % p)
        return p

    def sessions(self) -> Iterator[dict]:
        first = True
        for row in stream_jsonl(self._corpus_file("sessions.jsonl.gz")):
            if first:
                check_required(row, REQUIRED_SESSION_KEYS, "session")
                first = False
            yield row

    def steps(self) -> Iterator[dict]:
        first = True
        for row in stream_jsonl(self._corpus_file("agent_steps.jsonl.gz")):
            if first:
                check_required(row, REQUIRED_STEP_KEYS, "agent_step")
                first = False
            yield row

    def config_changes(self) -> List[dict]:
        out = []
        for r in read_csv(self._corpus_file("config_timeline.csv")):
            try:
                r["day"] = int(r["day"])
            except (KeyError, ValueError):
                continue
            out.append(r)
        out.sort(key=lambda r: (r["day"], r.get("tenant", ""), r.get("kind", "")))
        return out

    def feedback(self) -> List[dict]:
        p = os.path.join(self.corpus_dir, "feedback.csv")
        if not os.path.exists(p):
            p = os.path.join(self.root, "corpus", "feedback.csv")
        return read_csv(p)

    # --- labels ------------------------------------------------------------
    def outcome_labels(self) -> List[dict]:
        return read_jsonl(os.path.join(self.root, "labels", "outcome_labels.jsonl"))

    def rubric_scores(self) -> List[dict]:
        return read_jsonl(os.path.join(self.root, "labels", "rubric_scores.jsonl"))

    # --- catalog helpers -----------------------------------------------------
    def cardinality_budget(self, field: str) -> Optional[int]:
        budgets = self.catalog.get("cardinality_budgets") or {}
        if field in budgets:
            return int(budgets[field])
        f = (self.catalog.get("fields") or {}).get(field) or {}
        if "cardinality_budget" in f:
            return int(f["cardinality_budget"])
        return None

    def capability(self, cap_id: str) -> Dict:
        for c in self.catalog.get("capabilities") or []:
            if c.get("id") == cap_id:
                return c
        return {}

    def milestones(self) -> Dict[str, Dict[str, List[str]]]:
        return self.catalog.get("milestones") or {}


def format_change(c: dict) -> str:
    """One-line config marker. Target is often equal to kind (e.g. kind=kb,
    target=kb); saying both avoids the 'kb kb …' stutter in evidence strings."""
    return ("config_timeline day %d: kind=%s target=%s %s -> %s '%s'"
            % (c["day"], c.get("kind"), c.get("target"), c.get("from_value"),
               c.get("to_value"), c.get("note", "")))

