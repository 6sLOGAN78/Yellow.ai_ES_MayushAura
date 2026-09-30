# Planned advisory agent harness

**Status: architecture only — not implemented in this repository.**

Nexus Loop currently ends with a deterministic, validated `loop-report.json`
and a human decision screen. A future advisory agent can sit between those two
stages. Its job is to review the completed offline loop, consult the tenant's
knowledge base through a constrained harness, and draft an answer or suggestion
for the operator.

```text
logs → detect → diagnose → prescribe → verify
                                      ↓
                           validated loop report
                                      ↓
                    planned advisory-agent harness
                     ↙                          ↘
          read-only report access       read-only KB retrieval
                     ↘                          ↙
                cited answer / bounded suggestion
                                      ↓
                         human approve / reject / defer
```

## Harness contract

The harness would expose only these capabilities:

| Capability | Purpose |
|---|---|
| `report.read` | Read findings, evidence, coverage, refusals, prescriptions, and replay results from the validated report. |
| `kb.search` | Retrieve tenant-scoped knowledge-base passages relevant to an operator question. |
| `kb.read` | Read the selected passages and retain their document identifiers for citations. |
| `answer.draft` | Return a proposed answer or suggestion with citations, confidence, and any missing evidence. |

The agent's output is an **untrusted draft**. The operator remains responsible
for the decision.

## Required boundaries

The harness must not let the agent:

- edit the knowledge base or the generated report;
- invent a value for a missing metric or override a structured refusal;
- change metric definitions, coverage, judge versions, or the frozen golden set;
- call deployment, rollback, replay, approval, or production-write tools;
- write the final approve/reject/defer verdict.

Every answer should identify the report fields and KB document IDs it used. If
the available evidence cannot answer the question, the draft should return the
same explicit evidence gap rather than filling it with a plausible claim.

## Proposed exchange

```json
{
  "input": {
    "operator_question": "What should we change, and why?",
    "report_path": "out/loop-report.json",
    "tenant": "<tenant from the selected finding>"
  },
  "output": {
    "draft_answer": "<operator-facing suggestion>",
    "report_evidence": ["<finding or metric id>"],
    "kb_citations": ["<document id>"],
    "confidence": "high | medium | low",
    "missing_evidence": []
  }
}
```

No agent runtime, model call, KB connector, or `answer.draft` implementation is
shipped today. This document records the intended placement, permissions, and
honesty boundary for a later implementation.
