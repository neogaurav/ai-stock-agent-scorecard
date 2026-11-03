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
   checkable calls into the same schema. Needs ``pip install thesis-ledger[llm]``
   and ``ANTHROPIC_API_KEY``.
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

from .schema import Prediction, ValidationError

BLOCK_RE = re.compile(r"```(?:prediction|thesis-ledger)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
FIELD_NAMES = {f.name for f in fields(Prediction)}


def extract_blocks(text: str, defaults: dict[str, Any] | None = None) -> tuple[list[Prediction], list[str]]:
    preds, errors = [], []
    for raw in BLOCK_RE.findall(text):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"bad JSON in prediction block: {exc}")
            continue
        for item in data if isinstance(data, list) else [data]:
            try:
                preds.append(_build(item, defaults))
            except (ValidationError, TypeError, ValueError) as exc:
                errors.append(f"{item.get('ticker', '?')}: {exc}")
    return preds, errors


def _build(item: dict[str, Any], defaults: dict[str, Any] | None) -> Prediction:
    merged = {**(defaults or {}), **{k: v for k, v in item.items() if v is not None}}
    return Prediction.from_dict({k: v for k, v in merged.items() if k in FIELD_NAMES})


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
        raise RuntimeError("pip install 'thesis-ledger[llm]' to use LLM extraction") from exc
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model or os.environ.get("THESIS_LEDGER_MODEL", "claude-sonnet-5-5"),
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


def ingest_folder(folder: str | Path, ledger, *, use_llm: bool = False, patterns=("*.md", "*.txt"),
                  git_commit: bool = False) -> dict[str, Any]:
    """Seal predictions from every new or changed report in ``folder``.

    A file is skipped when its content hash was ingested before, so the watcher
    can run as often as you like.
    """
    folder = Path(folder)
    seen: dict[str, str] = json.loads(ledger.ingested_path.read_text()) if ledger.ingested_path.exists() else {}
    summary = {"files": 0, "sealed": 0, "errors": []}
    batch: list[Prediction] = []
    for pattern in patterns:
        for path in sorted(folder.rglob(pattern)):
            text = path.read_text("utf-8", errors="replace")
            digest = hashlib.sha256(text.encode()).hexdigest()
            if seen.get(str(path)) == digest:
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)
            defaults = {"issued_at": mtime.isoformat(), "thesis_run": path.stem}
            preds, errs = extract_blocks(text, defaults)
            if not preds and use_llm:
                preds, errs = extract_llm(text, defaults)
            batch.extend(preds)
            summary["errors"].extend(f"{path.name}: {e}" for e in errs)
            seen[str(path)] = digest
            summary["files"] += 1
    links = ledger.seal(batch, git_commit=git_commit)
    summary["sealed"] = len(links)
    ledger.ingested_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.ingested_path.write_text(json.dumps(seen, indent=2))
    return summary
