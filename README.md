# AI Stock Agent Scorecard

**Live dashboard: https://neogaurav.github.io/ai-stock-agent-scorecard-site/** · [demo with synthetic data](https://neogaurav.github.io/ai-stock-agent-scorecard/demo/)

**A lookahead-free, verifiable track record for AI stock-research agents.**

Backtests of LLM trading agents can't be trusted: the model has already read about what happened. AI Stock Agent Scorecard
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
| Python / CLI | `pip install "ai-stock-agent-scorecard[yfinance]"` or run without installing: `uvx --from ai-stock-agent-scorecard agent-scorecard --help` |
| Claude Code | `/plugin marketplace add neogaurav/ai-stock-agent-scorecard`, then `/plugin install ai-stock-agent-scorecard@ai-stock-agent-scorecard` |
| Any MCP client | `claude mcp add agent-scorecard -- uvx --from "ai-stock-agent-scorecard[mcp]" agent-scorecard mcp` |
| Just looking | `python examples/demo.py` builds a demo site from synthetic data in `demo-site/` |

## Quick start

```bash
agent-scorecard --root ledger init --title "My agents"
agent-scorecard --root ledger ingest path/to/agent/reports     # seal calls from ```prediction blocks
agent-scorecard --root ledger nightly --out site               # grade → reveal → build site
agent-scorecard --root ledger verify
```

Have your agents end each report with a block like this. Extraction is then deterministic:

````markdown
```prediction
{"ticker": "MU", "direction": "long", "entry": 228.5, "stop": 214, "target": 262,
 "horizon_days": 20, "confidence": 0.65, "price_at_issue": 233.1,
 "source_agent": "technicals", "claim": "Holds 20-day MA on pullback"}
```
````

For free-form reports, `ingest --llm` falls back to LLM extraction (`pip install "ai-stock-agent-scorecard[llm]"`, `ANTHROPIC_API_KEY`).

### When a thesis breaks: closing a call early

If something material changes after a call is made, the agent withdraws it with a close block
(or `agent-scorecard close MU --direction long --reason "..."`, or the `close_call` MCP tool):

````markdown
```close
{"ticker": "MU", "direction": "long", "source_agent": "event-reviewer",
 "reason": "Guide cut on the 8-K; the demand thesis no longer holds"}
```
````

The close is sealed in the same hash chain, so it is timestamped and can't be backdated. It applies to
live calls on that ticker issued before it, narrowed by `direction`, `agents` or an exact `ref` id.

- **Entered calls** exit at the **open of the next trading day** after the close, never at a price the agent picks.
  They count toward the win rate like any other trade (someone may have taken it), and their R feeds avg R.
- **Calls still waiting for entry** are **cancelled**: listed and counted, no R.
- If the stop or target was hit before the close took effect, the close changes nothing.
- Early closes get their own tab on the Trades page and their own count per agent, so an agent that bails
  on losers to look good is visible. The target-hit rate and calibration leave early closes out, because
  those calls never found out whether the stop or the target would come first.

### Re-entering after an exit

Every call is one trade. After it ends (stop, target, early close or expiry) the scorecard tracks nothing on that
ticker until an agent makes a new call. `agent-scorecard exits --state seen.json --out exits.json` lists the calls
that exited since the last run, so your research pipeline can review each one and either stand aside or re-enter.

A re-entry is a normal call with `"reentry_of": "<id of the call that exited>"`, and two rules apply:

- **It waits for a pullback.** It needs a limit `entry` below the current price for a long (above it for a short).
  Re-buying at market is just the old trade with a moved target, so it is refused.
- **No revenge trades.** A re-entry of a call that was **stopped out** is refused for `reentry_cooldown_days`
  (default 5) trading days. A fresh full analysis that issues a new call is not a re-entry and is not blocked.

Re-entries get a tag and their own tab on the Trades page, and their own average R per agent, so you can see
whether an agent's second bite is any good.

## Recommended setup (two repos, both free)

1. **`scorecard-data` (private).** Holds `private/` (records and salts) and `public/` (chain and reveals).
   A scheduled job on your machine seals new calls and pushes every 15 minutes, so GitHub records the time of
   each seal. On Windows, `examples/windows/setup-pc.ps1` installs everything and registers both jobs.
2. **`ai-stock-agent-scorecard-site` (public).** Contains only the built site, served by GitHub Pages.
   The nightly job (on your PC, or in the cloud via `examples/data-repo/.github/workflows/nightly.yml`)
   grades, reveals and publishes it.

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
hold, buy or sell long or short positions in any security on the site at any time, including before and after a call
is published; calls are not the author's personal recommendations. See the site footer for the full text.
(Per-ticker position badges can be switched on with `"position_badges": true` in the ledger's `config.json`.)

## Roadmap

- [ ] OpenTimestamps anchoring of the chain head
- [ ] Adapters for TradingAgents and ai-hedge-fund outputs
- [ ] Brokerage position sync (SnapTrade) for automatic author-position badges
- [ ] Benchmarks against buy-and-hold and random entries with the same stop and target

MIT licensed.
