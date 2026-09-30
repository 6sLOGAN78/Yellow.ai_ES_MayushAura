#!/usr/bin/env python3
"""Build several held-out (sealed) corpora to test candidates against.

Each corpus is generated with the kit's own generator and a different
passphrase, so the fault layout (days/lengths/rates) differs every time and we
hold the answer key locally to score candidates.

    python3 testbed/make_corpora.py \
        --generator /path/to/repo/tools/nexus-loop-kit/generate.py \
        --out-dir /tmp/nexus-testbed/kits \
        --secrets alpha-secret,bravo-secret,charlie-secret

A corpus directory contains catalog.json, corpus/, corpus_sample/,
ground_truth_SEALED/ground_truth.json, labels/, asks.md, manifest.json — the same
shape the practice kit has.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def default_generator() -> str:
    for c in (
        os.path.join(os.path.dirname(HERE), "nexus-loop-kit", "generate.py"),
        os.path.join(HERE, "nexus-loop-kit", "generate.py"),
        os.path.join(os.path.dirname(HERE), "tools", "nexus-loop-kit", "generate.py"),
        os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools", "nexus-loop-kit", "generate.py"),
        os.path.join(HERE, "..", "yellow-ai", "tools", "nexus-loop-kit", "generate.py"),
    ):
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(c)


def existing_kit(path: str) -> bool:
    return (os.path.exists(os.path.join(path, "catalog.json"))
            and os.path.exists(os.path.join(path, "ground_truth_SEALED", "ground_truth.json")))


def build(generator: str, out_dir: str, name: str, secret: str, scale: float,
          force: bool, rename: bool = False) -> str:
    dest = os.path.join(out_dir, name)
    if existing_kit(dest) and not force:
        print("skip  %s (exists; use --force to rebuild)" % dest)
        return dest
    os.makedirs(out_dir, exist_ok=True)
    cmd = [sys.executable, generator, "--sealed", secret, "--out", dest,
           "--scale", str(scale)]
    print("build %s -> %s" % (name, dest))
    subprocess.run(cmd, check=True)
    if not existing_kit(dest):
        raise SystemExit("generator did not produce a sealed kit at %s" % dest)
    if rename:
        from transform_kit import transform_kit
        m = transform_kit(dest, "x")
        print("  hard-mode: renamed %d tenant/intent/tool/agent tokens to opaque ids" % len(m))
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generator", default=default_generator(),
                    help="path to tools/nexus-loop-kit/generate.py")
    ap.add_argument("--out-dir", default="testbed/kits")
    ap.add_argument("--secrets", default="sealed-alpha,sealed-bravo,sealed-charlie",
                    help="comma-separated passphrases; each becomes one corpus")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="1.0 = full corpus; lower for speed")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--rename", action="store_true",
                    help="hard mode: rewrite tenant/intent/tool/agent names to opaque ids")
    a = ap.parse_args()

    names = []
    for i, secret in enumerate([s.strip() for s in a.secrets.split(",") if s.strip()]):
        name = "kit_%02d_%s" % (i + 1, secret[:12].replace("/", "_"))
        if a.rename:
            name += "_renamed"
        build(a.generator, a.out_dir, name, secret, a.scale, a.force, a.rename)
        names.append(name)
    print("\n%d corpora in %s" % (len(names), os.path.abspath(a.out_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
