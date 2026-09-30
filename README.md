# Nexus Loop — Yellow.ai TechQuest, Endterm Submission

**Team:** Mayush Aura · **Institution:** Indian Institute of Technology, Patna
**Event:** Yellow.ai TechQuest — Endterm Submission

This is the complete endterm submission package for **Nexus Loop**, a
deterministic quality-control loop that turns an already-deployed AI support
agent's logs into a short list of verified improvement decisions, and gives a
human operator one approve/reject action to take on each.

Everything below is self-contained and offline. No API keys, no network calls,
no model downloads.

**Planned extension (documentation only):** after the deterministic offline
loop, a constrained advisory-agent harness may read the validated report and a
tenant-scoped knowledge base to draft cited answers and suggestions for the
human operator. It is not implemented and cannot approve, deploy, or change
evidence. See `solution/docs/advisory-agent-harness.md`.

---

## 1. What is in this ZIP

| Path | What it is |
|---|---|
| `README.md` | This file — the full operating procedure (prerequisites, installation, setup, execution, expected output). |
| `Yellow.ai_ES_MayushAura_EndReport.pdf` | The Endterm report: progress, approach, methodology, findings, limitations (8 pages). |
| `Certificates/Yellow.ai_ES_MayushAura_PlagiarismReport.pdf` | Turnitin similarity (plagiarism) report for the End report. |
| `Certificates/Yellow.ai_ES_MayushAura_AICheckReport.pdf` | Turnitin AI-writing check report for the End report. |
| `solution/` | The current solution: full source code, the practice dataset, and all tooling. |
| `solution/out/loop-report.json` | A pre-generated report produced by the exact commands in §5, so you can compare your run against ours. |

**Naming convention.** Every deliverable file is prefixed
`Yellow.ai_ES_MayushAura_` (event + team) so its type is unambiguous:
`_EndReport.pdf`, `_PlagiarismReport.pdf`, `_AICheckReport.pdf`. The archive
itself follows the required `Yellow.ai_ES_<TeamName>.zip` form.

Inside `solution/`:

| Path | What it is |
|---|---|
| `run.py` | One-command entrypoint: loads the kit, detects, diagnoses, prescribes, verifies, validates, writes `out/loop-report.json`, and scores it. |
| `nexus_loop/` | The solution package (ingestion, features, coverage, detectors, asks, standards, replay client, report assembly, validator, decision screen). |
| `nexus_loop/static/` | The operator decision screen (HTML/CSS/JS, fonts bundled offline). |
| `kit/` | The practice dataset: sessions, steps, config timeline, labels, catalog, asks, ground truth (~33 MB). |
| `tools/nexus-loop-kit/` | Organiser tooling: schema, scorer, and the replay service. |
| `tools/testbed/` | Held-out corpus generator and candidate evaluator. |
| `docs/` | The official problem statement, solution overview, findings-and-fixes log, honesty note, and planned advisory-agent harness contract. |

---

## 2. Prerequisites

| Requirement | Detail |
|---|---|
| **Python** | **3.9 or newer** (developed and tested on CPython 3.12). |
| **Third-party Python packages** | **None.** The entire pipeline and the web server use only the Python standard library. There is no `pip install` step. |
| **Browser** | Any modern browser (Chrome/Chromium/Firefox/Safari) to view the decision screen. |
| **Hardware** | Any laptop. No GPU. ~40 MB disk for the kit plus a few MB of output. |
| **Network** | Not required at any point. |
| **OS** | Linux, macOS, or Windows. Commands below use `python3`; on Windows use `py -3` or `python`. |

Optional, only to run the HTTP contract test suite:

| Requirement | Detail |
|---|---|
| **pytest** | `pip install pytest` (test suite only; the solution itself does not need it). |

---

## 3. Installation

There is nothing to install for the solution itself. Confirm Python is present:

```bash
python3 --version          # expect 3.9 or newer
```

Then move into the solution directory. All commands in §4–§6 are run from here:

```bash
cd solution
```

A virtual environment is optional, because no third-party packages are used. If
your environment requires one:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

(Optional) install pytest if you also want to run the test suite:

```bash
pip install pytest
```

---

## 4. Project setup

No configuration is required — the practice dataset ships inside `solution/kit/`,
so there is no dataset to place and no environment variables to set.

| Item | Default | How to change |
|---|---|---|
| Dataset | `solution/kit/` | `--kit /path/to/kit` |
| Output directory | `solution/out/` | `--out /path/to/loop-report.json` |
| Replay service URL | `http://127.0.0.1:8719` | `--replay-url http://host:port` |
| Decision screen port | `8765` | `--port <n>` |
| Team name written into the report | `nexus-loop` | `--team "Your Team"` |
| JSON schema | `tools/nexus-loop-kit/schema/loop-report.schema.json` | `--schema /path/schema.json` |

The `out/` directory is created automatically. The pipeline writes three files
there: `loop-report.json`, `golden_set.json`, and `replay.log` (and
`decisions.log` after a human decision).

**Ports used.** The replay service listens on `8719`; the decision screen
listens on `8765`. Both are configurable and only need to be free locally.

---

## 5. Execution

Run every command from inside `solution/`.

### Step 1 — (recommended) start the replay verification service

The pipeline verifies one proposed fix against a local replay service. In a
**separate terminal**, from `solution/`:

```bash
python3 tools/nexus-loop-kit/replay/serve.py --kit kit --port 8719
```

Leave it running. If you skip this step, add `--no-replay` to Step 2; the report
is still valid and simply omits the verification block.

### Step 2 — run the pipeline (cold run, unattended)

From `solution/`:

```bash
python3 run.py --kit kit --team "Mayush Aura" --out out/loop-report.json
```

This one command loads the kit, builds the session table, detects the
regressions, dismisses the lookalikes, answers the eleven operator asks, freezes
the golden set, proposes the fixes, verifies one on the replay service, validates
against the schema, writes `out/loop-report.json`, and scores it against the
practice ground truth.

Without the replay service:

```bash
python3 run.py --kit kit --team "Mayush Aura" --out out/loop-report.json --no-replay
```

### Step 3 — (optional) score the report explicitly

`run.py` scores automatically. To score a report on its own:

```bash
python3 tools/nexus-loop-kit/score.py \
  --report out/loop-report.json \
  --ground-truth kit/ground_truth/ground_truth.json
```

### Step 4 — open the operator decision screen

```bash
python3 -m nexus_loop.app --report out/loop-report.json --port 8765
```

Then open <http://127.0.0.1:8765> in a browser.

To produce the report and serve the screen in a single process instead:

```bash
python3 run.py --kit kit --team "Mayush Aura" --out out/loop-report.json --serve --port 8765
```

On the screen, use the four tabs (Findings, Refusals, Metrics, Decisions), pick
a finding from the left rail, type your name and a reason, and press **Approve**
or **Reject**. The verdict is written back into `out/loop-report.json`
atomically (`prescriptions[].approval`) and echoed to `out/decisions.log`.

### Step 5 — (optional) run the held-out testbed

```bash
python3 tools/testbed/make_corpora.py --out-dir /tmp/nexus-testbed/pool \
  --secrets pool-alpha,pool-bravo,pool-charlie
python3 tools/testbed/evaluate.py \
  --candidates tools/testbed/candidates.json \
  --kits-dir /tmp/nexus-testbed/pool --quick --explain
```

### Step 6 — (optional) run the HTTP contract tests

```bash
python3 -m pytest nexus_loop/test_app.py -q
```

---

## 6. Expected output

After **Step 2** you should see progress lines and end with a score block:

```
kit        kit
corpus     kit/corpus
team       Mayush Aura
out        out/loop-report.json
loading session table …
  80252 sessions, 56 days, tenants acme-bank,northwind-retail, 16 config changes, 0 orphan steps
  tool_call coverage: acme-bank=0.721, northwind-retail=0.931
detectors …
  3 regression(s): kb_gap/acme-bank …, tool_contract/northwind-retail …, efficiency/acme-bank …
  3 decoy(s): load/northwind-retail, traffic_mix/acme-bank, judge_change/*
asks …
  16 metrics, 3 gaps
freezing golden sets → /…/out/golden_set.json
replay http://127.0.0.1:8719 …
  replay p01_tool_contract_northwind: tool.validate get_order_status … -> improved (run …, before 0.7741 after 0.8574, golden True)
wrote      out/loop-report.json  (…s)
  findings 6  diagnoses 6  prescriptions 3  verifications 1  metrics 16  gaps 3

--- score.py ---
==========================================================================
NEXUS LOOP  ·  scorecard
  team    Mayush Aura
  corpus  A   (ground truth: variant A)
==========================================================================
Diagnostic accuracy     20.0 / 20   [########################]
Specificity             15.0 / 15   [########################]
Loop completeness        8.0 / 8    [########################]
Honesty                 12.0 / 12   [########################]
--------------------------------------------------------------------------
SUBTOTAL                55.0 / 55   (machine-scored)
--------------------------------------------------------------------------
```

The scorer at the end prints a per-item ledger and a subtotal of
**`55.0 / 55`** on the practice corpus. Exact counts and timings may vary
slightly by machine, but the subtotal and the three diagnoses are deterministic.

Files produced in `solution/out/`:

| File | Contents |
|---|---|
| `loop-report.json` | The deliverable report, valid against the supplied JSON schema. |
| `golden_set.json` | The frozen IDs of the reference ("golden") sessions, written before any prescription. |
| `replay.log` | The audit trail of the replay verification run. |
| `decisions.log` | Appears after you approve/reject on the screen. |

On **Step 4**, the screen renders the report with no network calls and shows the
silent tool failure first. Approving writes the decision back into the report;
reloading the page shows the recorded verdict.

If the replay service was not running and you did **not** pass `--no-replay`,
the report is still written; the verification block is simply empty.

---

## 7. Honesty notes (real / stubbed / simulated)

These are recorded in the report and in `solution/docs/submission-note.md`; they
are repeated here because they matter for evaluation:

- **Real:** session-grain feature table, per-tenant emission coverage, the eleven
  asks, three detector-specific analyses, three explicit decoy dismissals, the
  failover refusal with its required event, cost as recorded, the golden-set
  freeze, and the decision write-back.
- **Stubbed:** no language model is called for any fact the logs already record.
- **Simulated:** the replay service is a hypothesis verifier with a seeded effect
  model; its "before" is not this system's corpus measurement.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `python3: command not found` | Use `python` or `py -3`; install Python 3.9+. |
| `score: no ground truth next to the kit; skipped` | You pointed `--kit` at a directory without `ground_truth/`. Use the bundled `kit/`. |
| Report has an empty `verifications` list | The replay service was not running. Start it (§5 Step 1) or pass `--no-replay`. |
| `Address already in use` | Change `--port` (screen) or the serve.py `--port` (replay). |
| Screen shows "report not found" | Pass `--report out/loop-report.json` with the correct path, or run §5 Step 2 first. |
