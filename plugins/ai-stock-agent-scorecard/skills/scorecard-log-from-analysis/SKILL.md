---
name: scorecard-log-from-analysis
description: Use right after any stock or options analysis that states a trade idea with a stop (or invalidation level) and a target. Logs each falsifiable call to the AI Stock Agent Scorecard so it can be graded later.
---

# Log falsifiable calls to the AI Stock Agent Scorecard

When an analysis in this session ends with a concrete trade idea, log it before moving on.

1. Pull out every call that has **both** a stop/invalidation level **and** a target. Skip vague views ("looks constructive").
2. For each call, fill: `ticker`, `direction` (long/short), `entry` (null for "at market"), `stop`, `target`,
   `horizon_days` (trading days), `confidence` (0-1; use what the analysis stated, else 0.5),
   `price_at_issue` (last price used in the analysis), `source_agent` (which analyst produced it:
   `fundamentals`, `technicals`, `options-flow`, `pm`, or `human` for the user's own call), and a `claim` of 12 words or less.
3. Call the `log_prediction` tool once per call. Do not change levels to make a call look better, and never re-log an
   old call with new levels; a revised view is a new prediction.
4. Tell the user in one line how many calls were sealed and show the short commitment hash.

If the `log_prediction` tool is unavailable, append a fenced block to the report instead so `agent-scorecard ingest` picks it up:

```prediction
{"ticker": "MU", "direction": "long", "entry": 228.5, "stop": 214, "target": 262, "horizon_days": 20,
 "confidence": 0.65, "price_at_issue": 233.1, "source_agent": "technicals", "claim": "Holds 20-day MA on pullback"}
```
