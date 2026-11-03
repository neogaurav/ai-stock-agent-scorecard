"""Append-only, hash-chained ledger with salted commit-reveal.

Layout under the ledger root::

    private/records.jsonl   full predictions + salts        (never publish)
    private/ingested.json   files already ingested
    public/chain.jsonl      seq, sealed_at, commitment, prev, hash   (publish immediately)
    public/reveals.jsonl    record + salt for revealed predictions  (publish after delay)
    config.json             ledger settings

A commitment is ``sha256(salt + canonical_json(record))``. Publishing only the
commitment proves *when* a call was made without disclosing it; publishing the
record and salt later lets anyone recompute the commitment and check it sits in
the chain at the claimed time.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .schema import Prediction, canonical_json, parse_iso, utc_now_iso

GENESIS = "0" * 64

DEFAULT_CONFIG: dict[str, Any] = {
    "title": "AI Stock Agent Scorecard",
    "reveal_delay_days": 5,
    "reveal_on_resolve": True,
    "author": "",
    "positions_file": "positions.json",
}


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def commitment_for(record: dict[str, Any], salt: str) -> str:
    return sha256_hex(salt + canonical_json(record))


def link_hash(seq: int, sealed_at: str, commitment: str, prev: str) -> str:
    return sha256_hex(f"{seq}|{sealed_at}|{commitment}|{prev}")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(canonical_json(row) + "\n")


@dataclass
class VerifyReport:
    chain_length: int = 0
    reveals_checked: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class Ledger:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.private = self.root / "private"
        self.public = self.root / "public"
        self.records_path = self.private / "records.jsonl"
        self.ingested_path = self.private / "ingested.json"
        self.chain_path = self.public / "chain.jsonl"
        self.reveals_path = self.public / "reveals.jsonl"
        self.config_path = self.root / "config.json"

    # -- setup -------------------------------------------------------------
    def init(self, **overrides: Any) -> None:
        self.private.mkdir(parents=True, exist_ok=True)
        self.public.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            cfg = {**DEFAULT_CONFIG, **overrides}
            self.config_path.write_text(json.dumps(cfg, indent=2) + "\n", "utf-8")
        gi = self.root / ".gitignore"
        if not gi.exists():
            gi.write_text("# keep salts and unrevealed calls out of any public repo\nprivate/\n", "utf-8")

    @property
    def config(self) -> dict[str, Any]:
        if self.config_path.exists():
            return {**DEFAULT_CONFIG, **json.loads(self.config_path.read_text("utf-8"))}
        return dict(DEFAULT_CONFIG)

    # -- reads -------------------------------------------------------------
    def chain(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.chain_path)

    def records(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.records_path)

    def reveals(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.reveals_path)

    def predictions(self) -> list[Prediction]:
        return [Prediction.from_dict(r["record"]) for r in self.records()]

    def revealed_ids(self) -> set[str]:
        return {r["record"]["id"] for r in self.reveals()}

    # -- writes ------------------------------------------------------------
    def seal(self, predictions: Iterable[Prediction], *, sealed_at: str | None = None,
             git_commit: bool = False) -> list[dict[str, Any]]:
        """Seal predictions: write private record + salt, append public chain link."""
        preds = list(predictions)
        if not preds:
            return []
        self.init()
        chain = self.chain()
        known_ids = {r["record"]["id"] for r in self.records()}
        prev = chain[-1]["hash"] if chain else GENESIS
        seq = len(chain)
        stamp = sealed_at or utc_now_iso()
        links, private_rows = [], []
        for p in preds:
            if p.id in known_ids:
                continue  # idempotent: re-sealing the same call is a no-op
            if parse_iso(p.issued_at) > parse_iso(stamp) + timedelta(minutes=5):
                raise ValueError(f"{p.id}: issued_at is after the seal time")
            record = p.to_dict()
            salt = secrets.token_hex(16)
            commit = commitment_for(record, salt)
            h = link_hash(seq, stamp, commit, prev)
            links.append({"seq": seq, "sealed_at": stamp, "commitment": commit, "prev": prev, "hash": h})
            private_rows.append({"seq": seq, "commitment": commit, "salt": salt, "record": record})
            known_ids.add(p.id)
            prev, seq = h, seq + 1
        _append_jsonl(self.records_path, private_rows)
        _append_jsonl(self.chain_path, links)
        if links and git_commit:
            self._git_commit(f"seal {len(links)} prediction(s) · head {links[-1]['hash'][:12]}")
        return links

    def reveal_due(self, *, now: datetime | None = None, resolved_ids: set[str] | None = None,
                   git_commit: bool = False) -> list[dict[str, Any]]:
        """Reveal predictions whose delay has passed (or that have resolved)."""
        cfg = self.config
        now = now or datetime.now(timezone.utc)
        delay = timedelta(days=int(cfg["reveal_delay_days"]))
        resolved_ids = resolved_ids or set()
        already = self.revealed_ids()
        sealed_at = {link["commitment"]: link["sealed_at"] for link in self.chain()}
        out = []
        for row in self.records():
            rid = row["record"]["id"]
            if rid in already:
                continue
            due = parse_iso(sealed_at[row["commitment"]]) + delay <= now
            if due or (cfg["reveal_on_resolve"] and rid in resolved_ids):
                out.append({"commitment": row["commitment"], "salt": row["salt"],
                            "record_json": canonical_json(row["record"]), "record": row["record"],
                            "revealed_at": now.replace(microsecond=0).isoformat()})
        _append_jsonl(self.reveals_path, out)
        if out and git_commit:
            self._git_commit(f"reveal {len(out)} prediction(s)")
        return out

    # -- verification --------------------------------------------------------
    def verify(self) -> VerifyReport:
        return verify_public(self.chain(), self.reveals())

    def _git_commit(self, message: str) -> None:
        if not (self.root / ".git").exists():
            return
        subprocess.run(["git", "-C", str(self.root), "add", "public", "config.json"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", message], check=False)


def verify_public(chain: list[dict[str, Any]], reveals: list[dict[str, Any]]) -> VerifyReport:
    """Verify using only public artifacts, exactly as an outside reader would."""
    rep = VerifyReport(chain_length=len(chain))
    prev = GENESIS
    by_commit: dict[str, dict[str, Any]] = {}
    for i, link in enumerate(chain):
        if link["seq"] != i:
            rep.errors.append(f"seq gap at position {i}")
        if link["prev"] != prev:
            rep.errors.append(f"seq {i}: prev hash does not match previous link")
        if link_hash(link["seq"], link["sealed_at"], link["commitment"], link["prev"]) != link["hash"]:
            rep.errors.append(f"seq {i}: link hash mismatch (edited after sealing?)")
        prev = link["hash"]
        by_commit[link["commitment"]] = link
    for rv in reveals:
        rep.reveals_checked += 1
        rid = rv["record"].get("id", "?")
        # Hash the exact revealed string so any language can verify without
        # re-implementing Python's float formatting.
        record_json = rv.get("record_json") or canonical_json(rv["record"])
        if json.loads(record_json) != rv["record"]:
            rep.errors.append(f"{rid}: record_json and record disagree")
            continue
        if sha256_hex(rv["salt"] + record_json) != rv["commitment"]:
            rep.errors.append(f"{rid}: revealed record does not match its commitment")
            continue
        link = by_commit.get(rv["commitment"])
        if link is None:
            rep.errors.append(f"{rid}: commitment not found in chain")
            continue
        if parse_iso(rv["record"]["issued_at"]) > parse_iso(link["sealed_at"]) + timedelta(minutes=5):
            rep.errors.append(f"{rid}: issued_at is later than its seal time")
    return rep
