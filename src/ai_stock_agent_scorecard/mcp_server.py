"""MCP server so any agent (Claude Code, Claude Desktop, Cursor...) can write to the ledger.

Run with ``agent-scorecard mcp``. The ledger directory comes from ``SCORECARD_ROOT``.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .ledger import Ledger
from .schema import Close, Prediction, ValidationError
from .score import Outcome, scorecard

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore

server = _Server("ai-stock-agent-scorecard", instructions=(
    "Use log_prediction whenever an analysis states a falsifiable call with a stop and a target. "
    "Never edit or re-log a prediction to change its levels. If the thesis behind a live call breaks, "
    "call close_call first, then log_prediction for any new view."))


def _ledger() -> Ledger:
    led = Ledger(os.environ.get("SCORECARD_ROOT", "ledger"))
    led.init()
    return led


@server.tool()
def log_prediction(ticker: str, direction: str, stop: float, target: float, horizon_days: int,
                   confidence: float, price_at_issue: float, source_agent: str = "pm",
                   entry: float | None = None, claim: str = "", thesis_run: str | None = None) -> dict[str, Any]:
    """Seal one falsifiable prediction. direction is 'long' or 'short'; entry null means market entry;
    horizon_days is in trading days; confidence is 0-1. Returns the chain link (proof of time)."""
    try:
        p = Prediction(ticker=ticker, direction=direction, stop=stop, target=target, horizon_days=horizon_days,
                       confidence=confidence, price_at_issue=price_at_issue, source_agent=source_agent,
                       entry=entry, claim=claim, thesis_run=thesis_run)
    except ValidationError as exc:
        return {"ok": False, "error": str(exc)}
    links = _ledger().seal([p])
    return {"ok": True, "id": p.id, "link": links[0] if links else "already sealed"}


@server.tool()
def close_call(ticker: str, reason: str, direction: str | None = None, source_agent: str = "pm",
               agents: list[str] | None = None, ref: str | None = None) -> dict[str, Any]:
    """Withdraw live calls on a ticker because the thesis changed. An entered call exits at the next
    trading day's open; a call still waiting for entry is cancelled. Narrow with direction, agents or ref."""
    try:
        c = Close(ticker=ticker, reason=reason, direction=direction, source_agent=source_agent,
                  agents=agents or [], ref=ref)
    except ValidationError as exc:
        return {"ok": False, "error": str(exc)}
    led = _ledger()
    already = led.close_map()
    targets = [p.id for p in led.predictions() if c.matches(p) and p.id not in already]
    links = led.seal([c])
    return {"ok": True, "id": c.id, "withdraws": targets, "link": links[0] if links else "already sealed"}


@server.tool()
def get_scorecard() -> dict[str, Any]:
    """Scorecard over all sealed predictions using the last nightly grading run (private view)."""
    led = _ledger()
    path = led.private / "outcomes.json"
    outcomes = {k: Outcome(**v) for k, v in json.loads(path.read_text()).items()} if path.exists() else {}
    return scorecard(led.predictions(), outcomes)


@server.tool()
def verify_chain() -> dict[str, Any]:
    """Verify the public hash chain and every revealed prediction."""
    rep = _ledger().verify()
    return {"ok": rep.ok, "chain_length": rep.chain_length, "reveals_checked": rep.reveals_checked, "errors": rep.errors}


def run() -> None:  # pragma: no cover
    server.run()
