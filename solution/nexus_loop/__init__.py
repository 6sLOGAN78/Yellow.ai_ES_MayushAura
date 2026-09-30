"""Nexus Loop — deterministic agent-regression workbench.

Reads a kit corpus, builds a session-grain feature table, detects regressions
with detector-specific cohorts, dismisses lookalikes explicitly, answers the
eleven operator asks with a metric or a structured gap, mines a standard,
proposes one decision-shaped fix, verifies it on the replay endpoint and
writes a schema-valid ``loop-report.json`` that the decision screen renders.

Standard library only. No LLM is called anywhere in this package.
"""

__version__ = "0.1.0"
