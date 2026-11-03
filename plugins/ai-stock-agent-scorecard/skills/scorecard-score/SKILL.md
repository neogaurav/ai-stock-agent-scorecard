---
name: scorecard-score
description: Grade sealed predictions and summarise which agent is earning its keep. Use when the user asks "how are my agents doing", "score the ledger", or "show the scorecard".
---

# Score the ledger

1. Run `agent-scorecard score` (add `--json` for the full scorecard).
2. Summarise in plain language: overall hit rate and average R, the best and worst agent, any calibration bucket
   marked overconfident, and the insights list. Do not overstate small samples; say "too few" when n < 8.
3. If the user wants the public site refreshed, run `agent-scorecard nightly --out site` (score, reveal, rebuild).
