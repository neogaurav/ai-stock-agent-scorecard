---
name: ledger-seal
description: Seal predictions from a report file or folder into the Thesis Ledger. Use when the user says "seal", "log these calls", or "ingest my reports".
---

# Seal predictions

- One report or JSON file: run `thesis-ledger seal <file>`.
- A folder of agent reports (for example the PM agent's results folder): run `thesis-ledger ingest <folder>`.
  Add `--llm` to extract calls from free-form reports that have no ```prediction blocks.
- Then run `thesis-ledger verify` and report the chain length and head hash.

Use `--root <dir>` or the `THESIS_LEDGER_ROOT` environment variable to choose the ledger. Never edit files under
`private/` or `public/` by hand; the chain will fail verification.
