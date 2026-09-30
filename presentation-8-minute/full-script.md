# Full 8-Minute Script

## 01 - 0:00-0:15 - Trust agents with proof, not intuition

Today I am showing Nexus Loop: an assurance loop for AI agents. The core idea is simple. If agents are going to make operational decisions, we need evidence going in and accountable decisions coming out.

Transition: The question behind the product is this.

## 02 - 0:15-0:27 - The trust question

When every company depends on agents, who proves they are actually working? A green dashboard is not enough when the customer outcome is what matters.

Transition: This becomes urgent because agents are moving into real operations.

## 03 - 0:27-0:42 - Agents become operations

Agents now touch customer conversations, operations, knowledge, and decisions. As their autonomy increases, the control problem changes. The question is no longer only, "Did the system respond?" It is, "Did it help correctly?"

Transition: Here is the failure pattern we focused on.

## 04 - 0:42-0:57 - The invisible break

The dangerous failure is invisible. Systems can look healthy while outcomes drop. Logs show success, uptime stays high, and the dashboard says everything is fine, but users are getting worse answers.

Transition: Put yourself in the engineer's seat.

## 05 - 0:57-1:15 - The engineer's morning

An engineer opens the morning dashboard. Errors are green. Latency is fine. But resolution has dropped by 7.9 points, and there is no causal trail. The team knows something changed, but not what caused the damage.

Transition: In our testbed, that is exactly what happened.

## 06 - 1:15-1:33 - The failure looked healthy

The break was hidden inside empty-success tool calls. They returned HTTP 200, so they looked successful, but 13.7 percent had no useful result. Meanwhile, visible errors fell from 2.1 to 1.6 percent, while resolution dropped from 85.5 to 77.6.

Transition: The data existed. The cause was still missing.

## 07 - 1:33-1:48 - We have data, still lack cause

We have data: 80,252 sessions, 883,764 steps, 56 days, and 16 config changes. But the evidence is fragmented. We still lack the cause unless the system can connect behavior, change history, and outcomes.

Transition: That is why the dashboard is misleading.

## 08 - 1:48-2:06 - Healthy dashboard, falling outcomes

The operational dashboard says uptime is 99.98 percent, HTTP 200s are flowing, and error rate is only 1.6 percent. The outcome view says resolution is down 7.9 points, with 404 handoffs and 147 abandonments.

Transition: Nexus Loop is built around the missing links between those two views.

## 09 - 2:06-2:24 - Three missing links

There are three missing links. First, a signal that notices outcome regression. Second, a diagnosis that separates the real cause from decoys. Third, a safe decision path where humans approve what changes.

Transition: This is the product loop.

## 10 - 2:24-2:44 - Meet Nexus Loop

Nexus Loop is a deterministic offline assurance loop. It detects a failing cohort, diagnoses the likely cause, prescribes a bounded fix, verifies it through replay, prepares an agent-review recommendation, and leaves the final decision to a human.

Transition: Let me show the product moment.

## 11 - 2:44-3:14 - Product video

For the next 25 seconds, watch how the loop moves from a hidden regression to a reviewable decision.

[Play the full video. Do not speak over the video.]

Transition: The important part is that the loop also knows when not to answer.

## 12 - 3:14-3:29 - The loop knows when not to answer

This is not a system that always invents a verdict. It distinguishes anomaly from regression, and question from answer. When evidence is insufficient, refusal is a feature.

Transition: The architecture keeps that discipline explicit.

## 13 - 3:29-3:51 - Architecture

The pipeline ingests events, normalizes them, analyzes outcome changes, explains likely causes, verifies proposed fixes, and writes one report: `loop-report.json`. The important guardrails are simple: no LLM-generated facts, and no automatic deployment.

Transition: The agent layer is a recommendation layer, not an authority layer.

## 14 - 3:51-4:13 - Agent evaluation and recommendation

The planned agent is read-only. It reads the report and tenant knowledge base, then drafts a bounded recommendation with citations. That gives reviewers a faster starting point without letting the model decide what ships.

Transition: Here is what the loop found in the testbed.

## 15 - 4:13-4:31 - Three faults, three traps

It found three true faults: a knowledge-base gap, a silent tool-contract break, and prompt inefficiency. It also rejected three decoys: traffic shift, load spike, and judge-boundary noise.

Transition: The clearest example is the silent tool break.

## 16 - 4:31-4:49 - Diagnosis chain

The diagnosis traces the regression to a tool contract change from version 3.2 to 3.3 on day 40. The tool returned HTTP 200, but 382 calls came back empty. That is why the product looked healthy while users failed.

Transition: The prescription is intentionally bounded.

## 17 - 4:49-5:09 - Proposal and verification

The fix is to treat empty HTTP 200 responses as failure, require result fields, and fall back cleanly. Replay moved the cohort from 77.4 to 85.7 across 302 simulated sessions, while the golden set stayed stable in 30 out of 30 sessions.

Transition: That leads to the product boundary.

## 18 - 5:09-5:27 - Automation boundary

Nexus Loop automates sensing, reasoning, action proposal, and verification. It does not automate authority. The human reviewer still approves, rejects, or defers, with a name and reason written into the audit trail.

Transition: Every claim carries a confidence shape.

## 19 - 5:27-5:42 - Confidence

The product is specific about what it knows. Three faults found, three traps dismissed, eight questions answered, one partial, and two refused. That honesty is part of the control surface.

Transition: The question set tests that behavior.

## 20 - 5:42-5:54 - Eleven questions

Across eleven evaluation questions, the system handled all eleven: eight answered fully, one partly refused, and two refused because the evidence was not strong enough.

Transition: The tests were designed to make shallow pattern matching fail.

## 21 - 5:54-6:09 - Test design

The testbed used generated seeds, opaque renaming, relocated faults, a clean third tenant, and sealed keys. The goal was to test whether the loop could reason from evidence, not memorize labels.

Transition: The validation stayed strong under that pressure.

## 22 - 6:09-6:27 - Validation

It passed 55 out of 55 practice checks and 6 out of 6 held-out checks, including renamed hard mode, moved faults, and an unseen tenant. That matters because the workflow survives changes in surface language.

Transition: For the operator, this becomes one decision surface.

## 23 - 6:27-6:45 - Operator experience

The reviewer sees the regressions, affected volume, baseline drop, recommended bounded fix, golden-set guard, and final verdict in one place. The experience is built for deciding, not hunting through scattered logs.

Transition: The product value is measured in avoided damage.

## 24 - 6:45-7:05 - Measured impact

For the tool-contract break, the affected volume was 2,686 conversations, with an estimated 213 lost resolutions, 404 handoffs, and 147 abandonments. Other issues had different signatures, like a KB gap and prompt-efficiency cost.

Transition: This is credible today, and the roadmap is clear.

## 25 - 7:05-7:20 - Credible today, designed to scale

The offline loop works today. The next stage is production scale: distributed state, streaming evaluation, durable history, role-based review, live replay, and deployment or rollback integration.

Transition: The roadmap keeps the same evidence contract.

## 26 - 7:20-7:38 - Production roadmap

Scaling this product means richer evidence and stronger operations, not looser automation. Streaming scale, real verification, and operational hardening all preserve the same promise: evidence before action.

Transition: So the closing idea is simple.

## 27 - 7:38-7:52 - Closing

When success lies, ask for evidence. Nexus Loop makes agent behavior visible, explainable, testable, and decidable. It turns a green dashboard into a defensible decision.

Transition: Thank you.

## 28 - 7:52-8:00 - Thank you

Thank you. Trust agents with evidence. Keep humans in the decision.

