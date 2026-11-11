"""Turn agent reports into Predictions.

Two paths:

1. **Fenced blocks (preferred, deterministic).** Ask your agents to end each
   report with one or more blocks like::

       ```prediction
       {"ticker": "MU", "direction": "long", "entry": 228.5, "stop": 214,
        "target": 262, "horizon_days": 20, "confidence": 0.65,
        "price_at_issue": 233.1, "source_agent": "technicals",
        "claim": "Holds 20-day MA on pullback"}
       ```

2. **LLM extraction (fallback).** For free-form reports, an LLM pulls out
   checkable calls into the same schema. Needs ``pip install ai-stock-agent-scorecard[llm]``
   and ``ANTHROPIC_API_KEY``.

To withdraw earlier calls when a thesis breaks, write a close block::

    ```close
    {"ticker": "MU", "direction": "long", "source_agent": "event-reviewer",
     "reason": "Guide cut; demand thesis no longer holds"}
    ```

(``{"action": "close", ...}`` inside a ``prediction`` or ``scorecard`` block works too.)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import Close, Prediction, ValidationError, is_close

BLOCK_RE = re.compile(r"```(prediction|scorecard|close)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
FIELD_NAMES = {f.name for f in fields(Prediction)}
CLOSE_FIELDS = {f.name for f in fields(Close)} | {"action"}


def extract_blocks(text: str, defaults: dict[str, Any] | None = None) -> tuple[list[Prediction | Close], list[str]]:
    preds, errors = [], []
    for fence, raw in BLOCK_RE.findall(text):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"bad JSON in prediction block: {exc}")
            continue
        for item in data if isinstance(data, list) else [data]:
            try:
                if fence.lower() == "close" or is_close(item):
                    preds.append(_build_close(item, defaults))
                    continue
                preds.append(_build(item, defaults))
            except (ValidationError, TypeError, ValueError) as exc:
                errors.append(f"{item.get('ticker', '?')}: {exc}")
    return preds, errors


def _build(item: dict[str, Any], defaults: dict[str, Any] | None) -> Prediction:
    merged = {**(defaults or {}), **{k: v for k, v in item.items() if v is not None}}
    return Prediction.from_dict({k: v for k, v in merged.items() if k in FIELD_NAMES})


def _build_close(item: dict[str, Any], defaults: dict[str, Any] | None) -> Close:
    merged = {**(defaults or {}), **{k: v for k, v in item.items() if v is not None}}
    if isinstance(merged.get("agents"), str):
        merged["agents"] = [merged["agents"]]
    return Close.from_dict({k: v for k, v in merged.items() if k in CLOSE_FIELDS})


LLM_PROMPT = """You extract falsifiable trading predictions from an analyst report.
Return ONLY a JSON array. Each element must have:
ticker, direction ("long"|"short"), entry (number or null for market entry),
stop, target, horizon_days (trading days, integer), confidence (0-1),
price_at_issue, source_agent (which analyst made the call, lowercase), claim (<= 12 words).
Only include calls that state BOTH a stop (or invalidation level) and a target.
If the report gives a range, use its midpoint. If confidence is not stated, use 0.5.
Do not invent levels that are not in the report. Return [] if there are none.

REPORT:
"""


def extract_llm(text: str, defaults: dict[str, Any] | None = None,
                model: str | None = None) -> tuple[list[Prediction], list[str]]:  # pragma: no cover - network
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("pip install 'ai-stock-agent-scorecard[llm]' to use LLM extraction") from exc
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model or os.environ.get("SCORECARD_MODEL", "claude-sonnet-5-5"),
        max_tokens=2000,
        messages=[{"role": "user", "content": LLM_PROMPT + text[:150_000]}],
    )
    body = "".join(getattr(b, "text", "") for b in msg.content).strip()
    body = body[body.find("["): body.rfind("]") + 1] or "[]"
    preds, errors = [], []
    for item in json.loads(body):
        try:
            preds.append(_build(item, defaults))
        except (ValidationError, TypeError, ValueError) as exc:
            errors.append(f"{item.get('ticker', '?')}: {exc}")
    return preds, errors


def _stable_id(p: Prediction, rel: str, index: int) -> str:
    """Same file + same block -> same id, so a rewritten report is never sealed twice."""
    h = hashlib.sha256(f"{rel}|{p.source_agent}|{index}".encode()).hexdigest()[:8]
    prefix = "close-" if isinstance(p, Close) else ""
    return f"{prefix}{p.ticker}-{p.source_agent}-{h}"


def trading_days_between(start: str, end: str) -> int:
    """Weekdays after ``start`` up to and including ``end`` (ISO dates)."""
    from datetime import date, timedelta
    d, stop, n = date.fromisoformat(start[:10]), date.fromisoformat(end[:10]), 0
    while d < stop:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def reentry_problem(p: Prediction, ledger, outcomes: dict[str, Any] | None) -> str | None:
    """Why a re-entry can't be sealed yet, or None. ``outcomes`` maps call id to its graded Outcome."""
    if not getattr(p, "reentry_of", None):
        return None
    prev = next((q for q in ledger.predictions() if q.id == p.reentry_of), None)
    if prev is None:
        return f"re-entry of unknown call {p.reentry_of}"
    if prev.ticker != p.ticker:
        return f"re-entry of {p.reentry_of} is a different ticker ({prev.ticker})"
    o = (outcomes or {}).get(prev.id)
    if o is None or not o.resolved:
        return f"{p.reentry_of} has not exited yet, so there is nothing to re-enter"
    cooldown = int(ledger.config.get("reentry_cooldown_days") or 0)
    if o.status == "STOPPED" and cooldown and o.exit_date:
        n = trading_days_between(o.exit_date, p.issued_at)
        if n < cooldown:
            return (f"{p.reentry_of} was stopped out on {o.exit_date}; re-entries wait {cooldown} trading days "
                    f"(only {n} passed). A fresh full analysis run can still make a new call.")
    return None


def ingest_folder(folder: str | Path, ledger, *, use_llm: bool = False, patterns=("*.md", "*.txt"),
                  git_commit: bool = False, now: datetime | None = None,
                  outcomes: dict[str, Any] | None = None) -> dict[str, Any]:
    """Seal predictions from every new or changed report in ``folder``.

    * A file whose content was ingested before is skipped, so this can run every few minutes.
    * Each block gets an id derived from its file path, so a report that is rewritten later
      (a resumed run) cannot replace a call that was already sealed.
    * Calls issued more than ``max_seal_lag_minutes`` before now are refused, not backfilled.
      On the first run this simply marks your existing reports as seen.
    """
    folder = Path(folder)
    now = now or datetime.now(timezone.utc)
    lag = int(ledger.config.get("max_seal_lag_minutes") or 0)
    seen: dict[str, str] = json.loads(ledger.ingested_path.read_text()) if ledger.ingested_path.exists() else {}
    summary = {"files": 0, "sealed": 0, "too_old": 0, "errors": []}
    batch: list[Prediction | Close] = []
    for pattern in patterns:
        for path in sorted(folder.rglob(pattern)):
            text = path.read_text("utf-8", errors="replace")
            digest = hashlib.sha256(text.encode()).hexdigest()
            if seen.get(str(path)) == digest:
                continue
            seen[str(path)] = digest
            summary["files"] += 1
            if not BLOCK_RE.search(text) and not use_llm:
                continue
            rel = path.relative_to(folder).as_posix()
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)
            defaults = {"issued_at": mtime.isoformat(), "thesis_run": str(Path(rel).parent.as_posix())}
            preds, errs = extract_blocks(text, defaults)
            if not preds and use_llm:
                preds, errs = extract_llm(text, defaults)
            summary["errors"].extend(f"{rel}: {e}" for e in errs)
            for i, p in enumerate(preds):
                p.id = _stable_id(p, rel, i)
                if lag and (now - datetime.fromisoformat(p.issued_at)).total_seconds() > lag * 60:
                    summary["too_old"] += 1
                    continue
                why = None if isinstance(p, Close) else reentry_problem(p, ledger, outcomes)
                if why:
                    summary["errors"].append(f"{rel}: refused {p.ticker} re-entry: {why}")
                    continue
                batch.append(p)
    links = ledger.seal(batch, sealed_at=now.replace(microsecond=0).isoformat(), git_commit=git_commit)
    summary["sealed"] = len(links)
    ledger.ingested_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.ingested_path.write_text(json.dumps(seen, indent=2))
    return summary
