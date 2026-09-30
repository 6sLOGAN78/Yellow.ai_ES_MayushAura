#!/usr/bin/env python3
"""Hard-mode transform: rewrite a generated kit with neutral labels.

The shipped generator fixes the tenant, cohort and cause-class layout, so a
solution can pass by recognising names instead of structure. This transform
rewrites every tenant key, intent, tool and agent id in a generated kit to an
opaque token, *in place*, preserving all statistics. Detection that survives it
is name-agnostic; detection that does not was relying on literals.

It rewrites, consistently:
  corpus/*.jsonl.gz, corpus/*.csv, corpus_sample/*.jsonl.gz, labels/*.jsonl,
  catalog.json, manifest.json, ground_truth_SEALED/ground_truth.json

Only exact string matches on known tokens are replaced (not substrings), so
free text, versions and event names are untouched. A ``_transform.json`` records
the mapping.

    python3 tools/testbed/transform_kit.py --kit /tmp/kits/kit_01 --prefix x
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import shutil
from typing import Dict, List


def _read_gz_lines(path: str):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _write_gz_lines(path: str, rows: List[dict]) -> None:
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def remap(obj, mapping: Dict[str, str]):
    if isinstance(obj, dict):
        return {(mapping.get(k, k) if isinstance(k, str) else k): remap(v, mapping)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [remap(v, mapping) for v in obj]
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    return obj


def token_map(kit: str, prefix: str) -> Dict[str, str]:
    cat = json.load(open(os.path.join(kit, "catalog.json"), encoding="utf-8"))
    tenants = set(cat.get("tenants", {}).keys())
    agents, tools, intents = set(), set(), set()
    for t in cat.get("tenants", {}).values():
        agents.update(t.get("agents", []))
        tools.update(t.get("tools", []))
    for im in (cat.get("milestones") or {}).values():
        intents.update(im.keys())
    m: Dict[str, str] = {}
    for i, t in enumerate(sorted(tenants)):
        m[t] = "%st%d" % (prefix, i + 1)
    for i, a in enumerate(sorted(agents)):
        m[a] = "%sag%d" % (prefix, i + 1)
    for i, f in enumerate(sorted(tools)):
        m[f] = "%sfn%d" % (prefix, i + 1)
    for i, k in enumerate(sorted(intents)):
        m[k] = "%si%d" % (prefix, i + 1)
    return m


def _remap_jsonl(path: str, mapping: Dict[str, str]) -> None:
    if os.path.exists(path):
        _write_gz_lines(path, [remap(r, mapping) for r in _read_gz_lines(path)])


def _remap_csv(path: str, mapping: Dict[str, str]) -> None:
    if not os.path.exists(path):
        return
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for row in rows:
            w.writerow([mapping.get(c, c) for c in row])
    os.replace(tmp, path)


def _remap_json(path: str, mapping: Dict[str, str]) -> None:
    if not os.path.exists(path):
        return
    obj = json.load(open(path, encoding="utf-8"))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(remap(obj, mapping), fh, indent=2)
    os.replace(tmp, path)


def transform_kit(kit: str, prefix: str = "x") -> Dict[str, str]:
    mapping = token_map(kit, prefix)
    for sub in ("corpus", "corpus_sample"):
        d = os.path.join(kit, sub)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if name.endswith(".jsonl.gz"):
                _remap_jsonl(p, mapping)
            elif name.endswith(".csv"):
                _remap_csv(p, mapping)
    for name in os.listdir(os.path.join(kit, "labels")):
        if name.endswith(".jsonl"):
            p = os.path.join(kit, "labels", name)
            with open(p, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            with open(p, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(remap(r, mapping)) + "\n")
    for rel in ("catalog.json", "manifest.json",
                os.path.join("ground_truth_SEALED", "ground_truth.json")):
        _remap_json(os.path.join(kit, rel), mapping)
    with open(os.path.join(kit, "_transform.json"), "w", encoding="utf-8") as fh:
        json.dump({"mode": "rename", "prefix": prefix, "mapping": mapping}, fh, indent=2)
    return mapping


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", required=True, help="generated kit directory (transformed in place)")
    ap.add_argument("--prefix", default="x", help="prefix for opaque tokens")
    ap.add_argument("--copy-to", default=None, help="transform a copy instead of in place")
    a = ap.parse_args()
    kit = a.kit
    if a.copy_to:
        shutil.copytree(kit, a.copy_to)
        kit = a.copy_to
    m = transform_kit(kit, a.prefix)
    print("renamed %d tokens in %s" % (len(m), kit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
