# Thesis Ledger

**Live dashboard (demo data for now): https://neogaurav.github.io/thesis-ledger/**

**A lookahead-free, verifiable track record for AI stock-research agents.**

Backtests of LLM trading agents can't be trusted: the model has already read about what happened. Thesis Ledger
grades agents only on predictions that were **sealed before the outcome**, so every number is lookahead-free
by construction. Anyone can check that, without trusting you.

```
agent report ─▶ extract calls ─▶ seal (salted hash → chain) ─▶ nightly: grade vs prices ─▶ reveal after delay ─▶ public scorecard
```

- **Commit-reveal hash chain.** Each call is published at once as a salted hash, which proves *when* it was made
  without revealing it. The full call is revealed after a delay (default 5 days) or once it resolves.
- **Honest grading.** Grading starts the day *after* the call. If a bar touches both stop and target, the stop counts.
  Gaps fill at the open. Results are in R-multiples (profit or loss as a multiple of the risk taken).
- **Per-agent attribution.** It shows which analyst (fundamentals, technicals, options flow, PM, or you as `human`)
  actually adds value, and over which horizons.
- **Calibration.** When an agent says it is 70% confident, does the call hit 70% of the time? There's also a Brier score.
- **Static public site.** The scorecard, a browser-side **Verify** page and the raw data are published free on
  GitHub Pages.

## Install

| You use | Install |
|---|---|
| Python / CLI | `pip install "thesis-ledger[yfinance]"` or run without installing: `uvx thesis-ledger --help` |
| Claude Code | `/plugin marketplace add neogaurav/thesis-ledger`, then `/plugin install thesis-ledger@thesis-ledger` |
| Any MCP client | `claude mcp add thesis-ledger -- uvx --from "thesis-ledger[mcp]" thesis-ledger mcp` |
| Just looking | `python examples/demo.py` builds a demo site from synthetic data in `demo-site/` |

## Quick start

```bash
thesis-ledger --root ledger init --title "My agents"
thesis-ledger --root ledger ingest path/to/agent/reports     # seal calls from ```prediction blocks
thesis-ledger --root ledger nightly --out site               # grade → reveal → build site
thesis-ledger --root ledger verify
```

Have your agents end each report with a block like this. Extraction is then deterministic:

````markdown
```prediction
{"ticker": "MU", "direction": "long", "entry": 228.5, "stop": 214, "target": 262,
 "horizon_days": 20, "confidence": 0.65, "price_at_issue": 233.1,
 "source_agent": "technicals", "claim": "Holds 20-day MA on pullback"}
```
````

For free-form reports, `ingest --llm` falls back to LLM extraction (`pip install "thesis-ledger[llm]"`, `ANTHROPIC_API_KEY`).

## Recommended setup (two repos, both free)

1. **`ledger-data` (private).** Holds `private/` (records and salts) and `public/` (chain and reveals).
   A scheduled job on your machine runs `ingest --git` and pushes every 15 minutes, so GitHub records the time of
   each seal. See `examples/windows/`.
2. **`thesis-ledger-site` (public).** Contains only the built site, served by GitHub Pages.
   `examples/data-repo/.github/workflows/nightly.yml` grades, reveals and deploys it every night.

The data repo must be private, because publishing `private/` would reveal calls early.

## Trust model: what this proves, and what it doesn't

- ✅ A revealed call is byte-for-byte what was committed. Any edit breaks `sha256(salt + record_json)`.
- ✅ Calls can't be inserted, removed or reordered after the fact without breaking every later link.
- ✅ Grading uses only revealed records and public prices, so anyone can recompute the scorecard.
- ⚠️ `sealed_at` is the sealer's own clock. Pushing promptly to GitHub and publishing the chain head nightly
  anchors it in practice. For stronger guarantees, anchor the head hash with OpenTimestamps (on the roadmap).
- ⚠️ Selection bias: log **every** call your agents make, not only the ones you liked. The ingest watcher does this
  automatically. Record whether you traded a call in `acted_on`; it does not decide whether the call is logged.

## Disclaimer

For educational and research purposes only. Predictions are generated automatically by AI agents to evaluate how
accurate those agents are. They are not investment advice. Trading involves substantial risk of loss. The author may
hold positions in securities discussed; positions are disclosed per ticker on the site. See the site footer for
the full text.

## Roadmap

- [ ] OpenTimestamps anchoring of the chain head
- [ ] Adapters for TradingAgents and ai-hedge-fund outputs
- [ ] Brokerage position sync (SnapTrade) for automatic author-position badges
- [ ] Benchmarks against buy-and-hold and random entries with the same stop and target

MIT licensed.
