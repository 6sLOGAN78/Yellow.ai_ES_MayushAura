#!/usr/bin/env python3
"""Generate a *moved-fault* held-out corpus.

The shipped `sealed_schedule` only moves fault **days/lengths/rates**; it keeps
each fault on the same tenant, intent, tool and agent. The problem statement,
however, says the scored corpus moves faults to different **tenants and slices**.
This script produces that harder case, without touching the shipped kit:

  1. copy the kit's generator (`generate.py` + `nlkit/`) to a temp tree;
  2. apply the `cross` placement profile by targeted, asserted string edits;
  3. run the patched generator;
  4. move its ground truth to `ground_truth_SEALED/` so the testbed can score it.

`cross` profile (relocations, keeping the same two tenants):

    fault            practice                     moved
    KB gap           acme / premium_card_info     northwind / sizing_help (existing intent, has a before-period)
    tool contract    northwind / get_order_status acme / block_card (card_block)
    prompt reg.      acme / acme_main_v3          northwind / nw_care_v3
    traffic mix      acme / branch_locator        northwind / product_availability
    load event       northwind                    acme

This is deliberately separate from `make_corpora.py`: if the organisers answer
that faults do **not** relocate, the normal sealed pool remains the right test
and this script is simply unused.

    python3 tools/testbed/generate_moved.py --out /tmp/nexus-testbed/moved/kit_cross
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCE = os.path.abspath(os.path.join(HERE, "..", "nexus-loop-kit"))


def sub_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(
            "moved profile: patch %r matched %d times (expected exactly 1). "
            "The kit generator source may differ; review generate_moved.py." % (label, n))
    return text.replace(old, new)


# ---- simulate.py: fault placement, mix intent, load tenant, premium injection ----
SIM_PATCHES = [
    ("sim-fault-flags",
     '''        f_kb_gap = (t.key == "acme-bank" and intent_key == "premium_card_info"
                    and self.in_window(day, "kb_gap_day", "kb_gap_len"))
        f_prompt = (t.key == "acme-bank" and agent_id == "acme_main_v3"
                    and self.in_window(day, "prompt_reg_day", "prompt_reg_len"))
        f_tool_win = (t.key == "northwind-retail" and intent_key == "order_status"
                      and self.in_window(day, "silent_tool_day", "silent_tool_len"))
        d_load = (t.key == "northwind-retail"
                  and self.in_window(day, "volume_spike_day", "volume_spike_len"))''',
     '''        f_kb_gap = (t.key == "northwind-retail" and intent_key == "sizing_help"
                    and self.in_window(day, "kb_gap_day", "kb_gap_len"))
        f_prompt = (t.key == "northwind-retail" and agent_id == "nw_care_v3"
                    and self.in_window(day, "prompt_reg_day", "prompt_reg_len"))
        f_tool_win = (t.key == "acme-bank" and intent_key == "card_block"
                      and self.in_window(day, "silent_tool_day", "silent_tool_len"))
        d_load = (t.key == "acme-bank"
                  and self.in_window(day, "volume_spike_day", "volume_spike_len"))'''),
    ("sim-silent-tool",
     '''                    silent = (f_tool_win and tool_key == "get_order_status"''',
     '''                    silent = (f_tool_win and tool_key == "block_card"'''),
    ("sim-volume-tenant",
     '''        if t.key == "northwind-retail" and v["volume_spike_day"] <= day < v["volume_spike_day"] + v["volume_spike_len"]:''',
     '''        if t.key == "acme-bank" and v["volume_spike_day"] <= day < v["volume_spike_day"] + v["volume_spike_len"]:'''),
    ("sim-mix-intent",
     '''        if t.key == "acme-bank":
            if v["mix_shift_day"] <= day < v["mix_shift_day"] + v["mix_shift_len"]:
                w["branch_locator"] *= v["mix_shift_mult"]          # D1
            if day >= v["kb_gap_day"]:
                ramp = min(1.0, (day - v["kb_gap_day"]) / 5.0)
                w["premium_card_info"] = 0.055 * ramp               # F1 cohort appears
        return w''',
     '''        if t.key == "northwind-retail":
            if v["mix_shift_day"] <= day < v["mix_shift_day"] + v["mix_shift_len"]:
                w["product_availability"] *= v["mix_shift_mult"]    # D1
        return w'''),
    ("sim-no-premium-inject",
     '''            imap = t.intent_map()
            if t.key == "acme-bank":
                imap["premium_card_info"] = W.ACME_PREMIUM_INTENT''',
     '''            imap = t.intent_map()'''),
]

# ---- world.py: guilty config changes and ground-truth cohorts ----
WORLD_PATCHES = [
    ("world-guilty-kb",
     '''        ConfigChange(v["kb_gap_day"], "acme-bank", "kb", "kb",
                     "kb_2026_06_a", "kb_2026_06_c", "premium card launch content pack"),''',
     '''        ConfigChange(v["kb_gap_day"], "northwind-retail", "kb", "kb",
                     "kb_2026_06_a", "kb_2026_06_c", "product knowledge gap"),'''),
    ("world-guilty-tool",
     '''        ConfigChange(v["silent_tool_day"], "northwind-retail", "tool", "get_order_status",
                     "3.2.0", "3.3.0", "order service migration"),''',
     '''        ConfigChange(v["silent_tool_day"], "acme-bank", "tool", "block_card",
                     "1.0.0", "1.1.0", "card service migration"),'''),
    ("world-guilty-prompt",
     '''        ConfigChange(v["prompt_reg_day"], "acme-bank", "prompt", "acme_main_v3",
                     "p_v3", "p_v4", "safety and confirmation wording"),''',
     '''        ConfigChange(v["prompt_reg_day"], "northwind-retail", "prompt", "nw_care_v3",
                     "p_v3", "p_v9", "safety and confirmation wording"),'''),
    ("world-gt-f1",
     '''                "cause_class": "kb.gap",
                "tenant": "acme-bank",
                "cohort": {"intent": "premium_card_info"},''',
     '''                "cause_class": "kb.gap",
                "tenant": "northwind-retail",
                "cohort": {"intent": "sizing_help"},'''),
    ("world-gt-f2",
     '''                "cause_class": "tool.contract_break",
                "tenant": "northwind-retail",
                "cohort": {"intent": "order_status", "tool": "get_order_status"},''',
     '''                "cause_class": "tool.contract_break",
                "tenant": "acme-bank",
                "cohort": {"intent": "card_block", "tool": "block_card"},'''),
    ("world-gt-f3",
     '''                "cause_class": "prompt.regression",
                "tenant": "acme-bank",
                "cohort": {"agent_id": "acme_main_v3"},''',
     '''                "cause_class": "prompt.regression",
                "tenant": "northwind-retail",
                "cohort": {"agent_id": "nw_care_v3"},'''),
    ("world-gt-d1",
     '''                "kind": "traffic_mix_shift",
                "tenant": "acme-bank",''',
     '''                "kind": "traffic_mix_shift",
                "tenant": "northwind-retail",'''),
    ("world-gt-d2",
     '''                "kind": "load_event",
                "tenant": "northwind-retail",''',
     '''                "kind": "load_event",
                "tenant": "acme-bank",'''),
]

# ---- optional: append a third, clean tenant (tests "more than two tenants")
THIRD_TENANT_PATCH = [
    ("world-third-tenant",
     "TENANTS = [ACME, NORTHWIND]",
     '''GLOBEX = Tenant(
    key="globex-telco", label="Globex Telecom", vertical="telecom",
    daily_sessions=340, v2_share=0.15,
    agents=["gl_care_v3", "gl_orders_v3", "gl_returns_v3"],
    tools=NORTHWIND.tools,
    intents=NORTHWIND.intents,
    kb_docs=140,
)
TENANTS = [ACME, NORTHWIND, GLOBEX]'''),
]


def patch_file(path: str, patches, label: str) -> None:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for name, old, new in patches:
        text = sub_once(text, old, new, "%s/%s" % (label, name))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="destination kit directory")
    ap.add_argument("--source", default=DEFAULT_SOURCE, help="kit tools dir")
    ap.add_argument("--profile", default="cross", choices=["cross"])
    ap.add_argument("--tenants", type=int, default=2, choices=[2, 3],
                    help="2 = the shipped two tenants; 3 = append a clean, unseen third tenant")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    if os.path.exists(a.out):
        shutil.rmtree(a.out)

    tmp = tempfile.mkdtemp(prefix="moved-kit-")
    try:
        shutil.copy2(os.path.join(a.source, "generate.py"), os.path.join(tmp, "generate.py"))
        shutil.copytree(os.path.join(a.source, "nlkit"), os.path.join(tmp, "nlkit"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        sim = os.path.join(tmp, "nlkit", "simulate.py")
        world = os.path.join(tmp, "nlkit", "world.py")
        patch_file(sim, SIM_PATCHES, "simulate.py")
        patch_file(world, WORLD_PATCHES, "world.py")
        if a.tenants == 3:
            patch_file(world, THIRD_TENANT_PATCH, "world.py")

        print("generating moved-fault corpus (profile=%s, tenants=%d) -> %s"
              % (a.profile, a.tenants, a.out))
        os.makedirs(a.out, exist_ok=True)
        rc = subprocess.call([sys.executable, os.path.join(tmp, "generate.py"),
                              "--seed", str(a.seed), "--out", a.out])
        if rc != 0:
            raise SystemExit("patched generator failed with exit %d" % rc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # the testbed discovers corpora by ground_truth_SEALED/
    src_gt = os.path.join(a.out, "ground_truth")
    dst_gt = os.path.join(a.out, "ground_truth_SEALED")
    if os.path.isdir(src_gt) and not os.path.isdir(dst_gt):
        shutil.move(src_gt, dst_gt)
    with open(os.path.join(a.out, "_placement.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "profile": a.profile,
            "tenants": a.tenants,
            "kb": {"tenant": "northwind-retail", "intent": "sizing_help"},
            "tool": {"tenant": "acme-bank", "intent": "card_block", "tool": "block_card"},
            "prompt": {"tenant": "northwind-retail", "agent": "nw_care_v3"},
            "mix": {"tenant": "northwind-retail", "intent": "product_availability"},
            "load": {"tenant": "acme-bank"},
        }, fh, indent=2)
    print("done: %s (ground truth at ground_truth_SEALED/)" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
