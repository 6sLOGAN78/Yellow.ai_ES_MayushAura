# Nexus Loop — solution testbed

A general harness for testing a **candidate solution** against **held-out
(sealed) corpora** whose answer keys we hold locally. It assumes nothing about
how a candidate is built — only that it honours the deliverables and the rules
in the problem statement. The question it answers with evidence:

> Will this solution find the right problems, avoid the lookalikes, and keep its
> honesty guarantees on a corpus it has never seen?

---

## 1. Why this exists

The practice kit ships with its answer key, so a solution can be tuned until it
scores well **on that one corpus**. The scored run, however, happens on a
different corpus. The testbed closes that gap: it generates many valid held-out
corpora and scores candidates against their keys, so tuning against the practice
layout cannot masquerade as generalisation.

### What is known about the held-out corpus

- The problem statement says day 6 runs on "a second dataset with the same kinds
  of problems planted on different days, tenants, and slices of traffic," with
  the answer key held by the organisers.
- The kit ships the **generator** (`tools/nexus-loop-kit/generate.py` and
  `tools/nexus-loop-kit/nlkit/`); the sealed layout is `sealed_schedule(secret)`.
  The generative process is public and reproducible — only the organisers'
  passphrase is hidden.
- Caveat: the shipped `sealed_schedule` keeps tenants, cohorts and cause classes
  fixed and moves only days/lengths/rates, while the problem statement says
  tenants and slices move too. The testbed therefore also enforces **generality**
  (no practice literals) rather than trusting the generator's fixed layout.

So we can build as many held-out corpora as we like. That is strong but not
perfect evidence — see §9.

---

## 2. Files

| File | Purpose |
|---|---|
| `make_corpora.py` | Generate N held-out corpora from the kit generator with different secrets |
| `transform_kit.py` | Hard mode: rewrite tenant/intent/tool/agent labels to opaque ids in place |
| `generate_moved.py` | Moved-fault corpora: relocate the planted faults to different tenants/slices |
| `evaluate.py` | Run, validate, score and judge candidates across corpora |
| `schema_check.py` | Independent draft-07 validator for `loop-report.json` |
| `candidates.example.json` | Candidate definition template (copy to `candidates.json`) |

---

## 3. Quick start

From the repository root:

```bash
# 1. build three held-out corpora (each ~16s, ~32 MB)
python3 tools/testbed/make_corpora.py \
  --generator tools/nexus-loop-kit/generate.py \
  --out-dir /tmp/nexus-testbed/kits \
  --secrets alpha-secret,bravo-secret,charlie-secret

# 2. describe the solution under test
cp tools/testbed/candidates.example.json tools/testbed/candidates.json
$EDITOR tools/testbed/candidates.json

# 3. run it
python3 tools/testbed/evaluate.py \
  --candidates tools/testbed/candidates.json \
  --kits-dir /tmp/nexus-testbed/kits \
  --tools tools/nexus-loop-kit \
  --json-out /tmp/nexus-testbed/result.json
```

`evaluate.py` and `make_corpora.py` locate `tools/nexus-loop-kit` automatically
whether the testbed sits at `<repo>/testbed` or `<repo>/tools/testbed`, so
`--tools`/`--generator` are optional when the layout is standard.

### Hard mode and a recorded matrix (recommended)

Build a pool that mixes ordinary held-out layouts with **hard-mode** layouts
where every tenant, intent, tool and agent label is renamed to an opaque id, then
record a pass/fail matrix:

```bash
# ordinary held-out layouts
python3 tools/testbed/make_corpora.py \
  --out-dir /tmp/nexus-testbed/pool \
  --secrets pool-alpha,pool-bravo,pool-charlie

# same generator, labels renamed to opaque ids (hard mode)
python3 tools/testbed/make_corpora.py \
  --out-dir /tmp/nexus-testbed/pool \
  --secrets hard-alpha,hard-bravo,hard-charlie --rename

# run them all and write a matrix
python3 tools/testbed/evaluate.py \
  --candidates tools/testbed/candidates.json \
  --kits-dir /tmp/nexus-testbed/pool \
  --quick \
  --json-out /tmp/nexus-testbed/pool-result.json \
  --matrix-out /tmp/nexus-testbed/pool-matrix.md
```

`--rename` is described in §13; the matrix format in §8.1.

---

## 4. Candidate contract

A candidate is one JSON entry:

```json
{
  "name": "example-solution",
  "workdir": "/abs/path/to/checkout",
  "run": "python3 run.py --kit {kit} --team {team} --out {out} --replay-url {replay_url} --no-score",
  "serve": "python3 -m nexus_loop.app --report {report} --port {port}",
  "source_globs": ["nexus_loop/**/*.py", "run.py"],
  "literal_allow_globs": ["nexus_loop/static/**", "**/test_*.py"],
  "replay": true
}
```

| Field | Meaning |
|---|---|
| `name` | Label used in output and as the replay team prefix |
| `workdir` | Directory the `run`/`serve` commands execute in (absolute preferred) |
| `run` | **One unattended command** that writes a schema-valid `loop-report.json` at `{out}` |
| `serve` | Optional command that serves the decision screen on `{port}` |
| `source_globs` | Files scanned for hardcoded practice literals |
| `literal_allow_globs` | Paths exempt from the literal scan (fixtures, tests, static assets) |
| `replay` | `false` if the candidate does not call the replay service |

Substitutions available in `run`/`serve`: `{kit}`, `{team}`, `{out}`, `{outdir}`,
`{replay_url}`, `{port}`.

A candidate may be any language or layout; the testbed only needs these
commands.

---

## 5. What each run does

For every candidate × every corpus:

1. **Starts the replay service** (`tools/nexus-loop-kit/replay/serve.py`) on a
   free port and passes its URL to the candidate, so verification is exercised
   for real.
2. **Runs the candidate cold**, with stdin closed, in its own output directory.
   In full mode it runs **twice** to compare reports for determinism.
3. **Requires** exit code 0 within `--timeout` and a `loop-report.json`.
4. **Validates** the report against `schema/loop-report.schema.json` using the
   testbed's own validator (never the candidate's).
5. **Scores** it with the organiser's `score.py --json` against the corpus's
   hidden key.
6. **Checks** faults, decoys, strays and deliverable invariants (§6).
7. **Scans** the candidate source for practice literals.
8. **Exercises the screen**: loads `/`, submits an approval, confirms it was
   written back into the report, and confirms a missing reason is rejected.

Servers are started in their own process group and terminated cleanly, so a run
leaves no stray processes.

---

## 6. Checks in detail

### 6.1 Fault detection (per fault)

Uses the organiser's own matcher (`score.py:match_fault`, `cohort_matches`,
`DETECT_GRACE`). A fault is **clean** only when the finding:

- matches the fault's tenant and cohort key,
- carries a linked diagnosis with the **exact** `cause_class`,
- starts within the detection grace (no lag beyond the scorer's allowance).

A finding matched to the wrong cause or the wrong cohort is reported as found
but not clean. This is stricter than the raw score, which still gives partial
credit.

### 6.2 Decoy handling (per decoy)

- **False alarm**: a regression finding in the decoy's window that does not match
  a real fault.
- **Explicit dismissal**: a non-regression finding with
  `not_a_regression_because` and a linked diagnosis whose cause class is the
  decoy's (`traffic_mix`, `load`, `judge_change`).
- **Clean** = not a false alarm **and** explicitly dismissed. Silence does not
  count.

### 6.3 Stray findings

Regression findings that match neither a fault nor a decoy are listed. They cost
specificity and usually signal an over-eager detector.

### 6.4 Deliverable invariants (beyond `score.py`)

- required sections and `team`/`corpus` present;
- every metric has `fidelity ∈ {measured, judged, derived}` and a
  `coverage{value,basis}`;
- every judged metric carries `calibration{agreement,n}` and never ≥ 0.995;
- A11 is `NOT_MEASURABLE` with `required_event = failover` at `step` grain and
  fields `from_target,to_target,reason,recovered`;
- A04 has at least one **measured** metric (never judged);
- A09 is either a `REQUIRES_NEW_JUDGE` gap or a judged metric;
- a `CARDINALITY_REFUSED` gap exists for `custom_dims.customer_ref`.

### 6.5 Generality (practice literals)

The scan collects tenant keys, intent keys, tool names and agent ids from the
corpus catalog (plus the ground-truth cohorts) and reports any occurrence in the
candidate's `source_globs`, excluding `literal_allow_globs`. Hardcoding practice
values is the classic way a solution passes one corpus and fails a moved layout.

### 6.6 Determinism

In full mode the candidate runs twice; the reports must be identical after
dropping timestamp-like fields. Non-determinism makes the sealed run
irreproducible.

### 6.7 Screen mechanics

The testbed checks the screen **starts**, **renders HTML**, **writes an approval
back into the report**, and **requires a reason** for a decision. It does not
judge whether the screen is well designed — that is the human score.

---

## 7. Pass criteria

A candidate passes a corpus only when **all** hold:

1. one cold command exits 0 and writes the report;
2. the report is schema-valid;
3. every fault is clean (right cohort, right cause, no excess lag);
4. every decoy is explicitly dismissed and never falsely flagged;
5. no stray findings;
6. honesty = 12/12 and the deliverable invariants hold;
7. two cold runs are identical (full mode);
8. no practice literals in the candidate source;
9. a real, improved `rp_*` verification when a replay URL was provided;
10. the screen renders, writes back, and rejects a missing reason.

The machine score is printed for information but is **not** the bar. A solution
can score below 55 on a held-out corpus and pass (small detection-lag decay), or
score 55 on the practice corpus and fail a held-out layout (a missed cause or a
missed dismissal). The point is generalisation, not a single number.

`OVERALL` is `ALL CANDIDATES PASS` only if every candidate passes every corpus;
the process exits non-zero otherwise, so it can gate a merge or a release.

---

## 8. Interpreting the output

```
CANDIDATE example-solution   (/path/to/checkout)
  kit_01_alpha     PASS  machine=54.6/55  faults=/// decoys=///
  kit_02_bravo     FAIL  machine=53.5/55  faults=/// decoys=x// | decoy D1 not explicitly dismissed
```

- `faults=///` — F1/F2/F3 clean; `x` marks a fault that is missing, mis-cohorted,
  mis-caused, or late.
- `decoys=///` — D1/D2/D3 clean; `x` marks a false alarm or a missing dismissal.
- `| ...` — the first few failure reasons.
- `--json-out FILE` writes the full detail: schema problems, per-fault lag and
  cause, per-decoy flags, stray ids, invariant errors, literal hits, the raw
  `score.py` sections, and screen results. This is the artifact to attach to a
  review.

### 8.1 Matrix output

`--matrix-out FILE` writes a markdown table, one row per corpus and one column
per candidate, with the verdict and machine score:

```markdown
| corpus | candidate-a | candidate-b |
|---|---|---|
| kit_01_pool-alpha  | PASS (54.6) | FAIL (50.1) |
| kit_02_hard-bravo  | PASS (53.9) | PASS (54.2) |
```

Use it as the headline evidence for "we believe this survives the sealed run" —
the detailed per-fault/per-decoy breakdown lives in the `--json-out` file.

### 8.2 Point ledger (`--explain`)

The JSON already carries `score.py`'s per-criterion notes, but `--explain`
prints a compact ledger that names **exactly which points were lost and what the
candidate said**, so a failure can be handed to the author without guesswork:

```
machine 51.20/55  (lost 3.80)
Diagnostic accuracy  17.67/20  lost 2.33
    HIT   F3  lag=0d  cohort=ok  cause=model.change  -> 4.3/6.7
Specificity          13.53/15  lost 1.47
    QUIET D1 — not flagged, but never examined either (partial credit)
...
LOST F3 2.33 — candidate finding f03_efficiency_xt1 said cause=model.change,
     expected prompt.regression; cohort_ok=True lag=0
     cohort={'agent_id': 'xag4'} window={'from_day': 9, 'to_day': 20}
     attributed={'kind': 'model', 'day': 9, ...} (wanted {'kind': 'prompt', 'day': 9})
LOST D1 1.47 — never explicitly dismissed (no non-regression finding with a
     linked traffic_mix diagnosis)
```

`res["explain"]` in the JSON holds the same lines for every corpus.

---

## 9. What the testbed cannot prove

- **It is not the organisers' secret.** Passing N held-out corpora raises
  confidence; it does not guarantee the real one.
- **Tenant/slice moves are not reproduced** by the shipped generator. The
  literal scan is a proxy; it will not catch subtler assumptions (e.g. "exactly
  two tenants", "the KB fault always has no before-period").
- **Rules 1 and 2 are not machine-checkable here.** Inspect code for LLM use on
  logged facts, and judge by hand whether any yardstick moved.
- **Screen quality is partly human.** Mechanics are checked; comprehension and
  decision quality are not.
- **The literal scan is heuristic** — substring matching over source globs. It
  can miss obfuscated literals and can flag legitimate taxonomy code.

Use it as a strong filter, not a guarantee.

---

## 10. Recommended workflow

1. Build **at least three** corpora with different secrets; more is better,
   because different layouts exercise different cross-fault interactions.
2. Run `--quick` first (one run per corpus) to iterate fast.
3. Fix or reject anything that fails; re-run.
4. Run the survivors in full mode (determinism) with `--json-out`.
5. Attach the JSON to the review; treat a solution as sealed-ready only when it
   passes every corpus.

---

## 11. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `no sealed kits in ...` | Run `make_corpora.py` first, or pass `--kits-dir` |
| candidate reports not found | Check `workdir` and the `{out}` substitution in `run` |
| replay server never healthy | Port conflict or wrong `--tools`; check the kit path |
| screen not detected | `serve` command or wrong port substitution |
| candidate times out | Raise `--timeout`; full corpus runs take seconds-to-minutes |
| stray servers after a crash | They are started in their own process group; kill by port if a hard crash occurs |

## 12. Extending the checks

`evaluate.py` is plain stdlib Python. To add a check:

- objective/fault logic: extend `objective()`;
- deliverable rules: extend `invariants()`;
- candidate-level rules: extend `verdict()`.

Keep new checks implementation-agnostic — they should pass any solution that
follows the written deliverables, not one particular design.

---

## 13. Hard mode: moving tenants and slices

The shipped simulator fixes which tenant, cohort and cause class carry each
fault; `sealed_schedule` only moves days/lengths/rates. The problem statement,
however, says the scored corpus moves tenants and slices too. Hard mode closes
that gap as far as the shipped generator allows.

`transform_kit.py` (used automatically by `make_corpora.py --rename`) rewrites
every **tenant key, intent, tool and agent id** in a generated kit to an opaque
token (`xt1`, `xi10`, `xfn9`, `xag4`, ...), in place and consistently across:

- `corpus/*.jsonl.gz`, `corpus/*.csv`, `corpus_sample/*.jsonl.gz`;
- `labels/*.jsonl`;
- `catalog.json`, `manifest.json`;
- `ground_truth_SEALED/ground_truth.json` (tenants, cohorts, coverage keys).

Only **exact token matches** are replaced (not substrings), so free text,
version strings and event names are untouched. A `_transform.json` in the kit
records the mapping. All statistics are preserved — only labels change — so a
solution that detects structure rather than names passes unchanged, while a
solution that keys off `acme-bank` / `order_status` / `get_order_status` fails.

```bash
# in place
python3 tools/testbed/transform_kit.py --kit /tmp/kits/kit_01
# transform a copy
python3 tools/testbed/transform_kit.py --kit /tmp/kits/kit_01 --copy-to /tmp/kits/kit_01_hard
```

Limitation: a true **tenant swap** (moving the fault to the other tenant) is not
reproducible from the shipped simulator, because fault placement is hardcoded to
tenant keys. Neutral renaming plus the literal scan is the practical proxy; it
catches name-based assumptions even though it does not move which tenant is
affected.

---

## 14. Rule checks and evidence provenance

Two problem-statement rules and one anti-fabrication check are enforced in
addition to the functional checks.

### Rule 1 — no LLM for logged facts

`evaluate.py` scans the candidate source for LLM/embedding SDKs and provider
endpoints (`openai`, `anthropic`, `google.generativeai`, `cohere`, `litellm`,
`transformers`, ...). A model may legitimately be used for questions of
*meaning*, so this is reported as an audit note by default; `--strict-rules`
turns any reference into a failure. The recorded hits list is in the JSON
output under `rules.llm_deps`.

### Rule 2 — the yardstick is frozen

A solution must never change metric definitions, the judge, or the reviewed good
set to make a result look better. The testbed flags any source line that opens a
protected artifact (`catalog.json`, `ground_truth`, `loop-report.schema`,
`score.py`) in a write mode. A hit fails the candidate (`rules.yardstick_write`).

### Evidence provenance

The candidate never sees the sealed answer key: it is run against a copy of the
kit with `ground_truth*` removed, and the key is passed only to `score.py`.
(Reading `{kit}/ground_truth_SEALED/ground_truth.json` now fails.)

When a replay URL is provided, the testbed fetches the replay service's own
`GET /log` after the run and reconciles every `verifications[]` entry against it:

- the `replay_run_id` must have been issued by that service during this
  candidate's run (`foreign_replay_ids` otherwise); and
- the reported `verdict`, `before` and `after` must match the logged row, and the
  `prescription_id` must reference a real prescription (`replay_mismatches`
  otherwise).

Both fail the candidate, so invented run ids and runs obtained with a wrong fix
(but reported as `improved`) are caught.

### Network scope

Only the replay service is expected to be reached. The rule scan reports network
client usage for review; the workflow is otherwise offline, so a run that
depends on external network access will fail or stall and be caught by the
timeout.

### Robustness and strictness

- **Timeouts.** A candidate that exceeds `--timeout` is recorded as a failed run
  (no traceback, other candidates/corpora continue).
- **Literal universe.** The literal scan uses the union of every tenant, intent,
  tool and agent name across the practice kit *and* every corpus under test, so
  practice names are still caught on moved/renamed corpora. `source_globs` still
  decides which files are scanned; include non-`.py` source/config files if a
  candidate keeps names outside Python.
- **Skipped checks are recorded.** `result["skipped"]` lists anything not
  exercised (`--quick`, `--no-screen`, `--no-replay`, a candidate with no
  `serve`, or an unavailable replay server), so an all-PASS line cannot hide a
  check that never ran.
- **Schema backend.** `result["schema_backend"]` reports `jsonschema` or the
  built-in subset; the built-in ignores `additionalProperties`, so install
  `jsonschema` for full draft-07 validation.
- **Strictness is intentional.** `invariants()` is deliberately stricter than
  `score.py` (exact A11 fields, a judged metric must publish calibration, the
  customer_ref cardinality refusal). Pass `--lenient-invariants` to gate on
  schema + score only and treat the extras as advisories.

---

## 15. Moved-fault corpora (`generate_moved.py`)

The shipped `sealed_schedule` only moves fault **days/lengths/rates**; tenants,
cohorts and cause classes stay put. The problem statement says the scored corpus
moves faults to different **tenants and slices** too. `generate_moved.py`
produces that harder case, separately from the ordinary pool so nothing is lost
if the organisers confirm faults do **not** relocate.

It copies the kit's `generate.py` + `nlkit/` to a temp tree, applies the `cross`
placement profile through **asserted** string edits (each patch must match
exactly once, or it aborts), runs the patched generator, and moves the ground
truth to `ground_truth_SEALED/` so the normal testbed can score it.

### `cross` profile — what moves

| fault | practice | moved |
|---|---|---|
| KB gap | acme / `premium_card_info` (born broken) | northwind / `sizing_help` (existing intent, **has** a before-period) |
| tool contract | northwind / `get_order_status` | acme / `block_card` (`card_block`) |
| prompt regression | acme / `acme_main_v3` | northwind / `nw_care_v3` |
| traffic mix | acme / `branch_locator` | northwind / `product_availability` |
| load event | northwind | acme |

So each fault role lands on the opposite tenant and on a different cohort than
practice, and the KB fault is tested in its harder "existing intent" form rather
than the born-broken form the practice corpus always shows.

```bash
python3 tools/testbed/generate_moved.py --out /tmp/nexus-testbed/moved/kit_cross
python3 tools/testbed/evaluate.py \
  --candidates tools/testbed/candidates.json \
  --kits-dir /tmp/nexus-testbed/moved --explain
```

### Result

Against the `anupamgt-sol-v1` solution, the moved-fault corpus scores **55.0/55**:
all three faults found with the correct cohort and cause at lag 0, all three
lookalikes explicitly dismissed, no false alarms, strays, honesty gaps or
non-determinism, replay verification improved, screen write-back working. So the
detectors are not keyed to practice tenants/slices.

### More than two tenants (`--tenants 3`)

Pass `--tenants 3` to append a clean, unseen third tenant (`globex-telco`) with
its own agents and no planted fault. This tests the remaining assumption — that
the detectors do not assume exactly two tenants — and whether a clean tenant
produces stray findings.

Result against `anupamgt-sol-v1`: **55.0/55** on a 96,547-session, three-tenant
corpus (all faults at lag 0, all lookalikes dismissed, no strays from the clean
tenant, determinism/replay/screen pass). Note the scorer's tool-coverage line
reads `4/6` because the third tenant's tool coverage (1 − v2_share = 0.85) is not
one of the catalog's declared acme/northwind values; the check still passes since
the acme/northwind tool metrics match.

### Limits

- It relocates within the two shipped tenants and can add a clean third tenant.
  A tenant with **genuinely new intents/tools/milestones**, or a different
  tenant count with faults on the new tenants, is not reproducible from the
  shipped simulator (the whole kit is built on the two-tenant world).
- The patches are asserted against the current kit source; if the organisers ship
  a different generator, `generate_moved.py` aborts rather than silently
  producing a wrong corpus.
- `cross` is the only profile implemented; add profiles by extending the patch
  lists (each must match exactly once).

