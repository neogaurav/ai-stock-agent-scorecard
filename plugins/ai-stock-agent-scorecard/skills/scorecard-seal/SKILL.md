---
name: scorecard-seal
description: Seal predictions from a report file or folder into the AI Stock Agent Scorecard. Use when the user says "seal", "log these calls", or "ingest my reports".
---

# Seal predictions

- One report or JSON file: run `agent-scorecard seal <file>`.
- A folder of agent reports (for example the PM agent's results folder): run `agent-scorecard ingest <folder>`.
  Add `--llm` to extract calls from free-form reports that have no ```prediction blocks.
- Then run `agent-scorecard verify` and report the chain length and head hash.

Use `--root <dir>` or the `SCORECARD_ROOT` environment variable to choose the ledger. Never edit files under
`private/` or `public/` by hand; the chain will fail verification.
