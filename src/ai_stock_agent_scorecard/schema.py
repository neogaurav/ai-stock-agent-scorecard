"""Prediction schema: the unit the ledger seals, reveals and grades."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

DIRECTIONS = ("long", "short")


class ValidationError(ValueError):
    """Raised when a prediction is not checkable as written."""


def canonical_json(obj: Any) -> str:
    """Deterministic JSON used for every hash in the ledger."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class Prediction:
    """A falsifiable call: direction, levels, horizon and stated confidence.

    ``entry`` of ``None`` means "enter at ``price_at_issue``" (a market entry).
    Otherwise the prediction only becomes live once price trades through ``entry``
    within the horizon.
    """

    ticker: str
    direction: str
    stop: float
    target: float
    horizon_days: int
    confidence: float
    price_at_issue: float
    source_agent: str = "pm"
    entry: float | None = None
    issued_at: str = field(default_factory=utc_now_iso)
    claim: str = ""
    thesis_run: str | None = None
    acted_on: bool | None = None
    tags: list[str] = field(default_factory=list)
    reentry_of: str | None = None
    id: str = ""

    def __post_init__(self) -> None:
        self.ticker = self.ticker.strip().upper()
        self.direction = self.direction.strip().lower()
        self.source_agent = self.source_agent.strip().lower() or "pm"
        self.issued_at = parse_iso(self.issued_at).isoformat()
        for name in ("stop", "target", "price_at_issue"):
            setattr(self, name, float(getattr(self, name)))
        if self.entry is not None:
            self.entry = float(self.entry)
        self.horizon_days = int(self.horizon_days)
        self.confidence = float(self.confidence)
        if self.confidence > 1:  # accept "65" as 65%
            self.confidence = self.confidence / 100.0
        self.validate()
        if not self.id:
            self.id = self.default_id()

    @property
    def entry_ref(self) -> float:
        return self.entry if self.entry is not None else self.price_at_issue

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry_ref - self.stop)

    @property
    def planned_r(self) -> float:
        return abs(self.target - self.entry_ref) / self.risk_per_share

    def validate(self) -> None:
        if not self.ticker:
            raise ValidationError("ticker is required")
        if self.direction not in DIRECTIONS:
            raise ValidationError(f"direction must be one of {DIRECTIONS}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValidationError("confidence must be between 0 and 1")
        if self.horizon_days < 1:
            raise ValidationError("horizon_days must be at least 1")
        if min(self.stop, self.target, self.price_at_issue) <= 0:
            raise ValidationError("prices must be positive")
        e = self.entry_ref
        if self.direction == "long" and not (self.stop < e < self.target):
            raise ValidationError(f"long needs stop < entry < target (got {self.stop} / {e} / {self.target})")
        if self.direction == "short" and not (self.target < e < self.stop):
            raise ValidationError(f"short needs target < entry < stop (got {self.target} / {e} / {self.stop})")
        if self.reentry_of:
            # A re-entry waits for a pullback: a limit below the current price (above it for a short).
            # Re-buying at market after an exit is just the old trade with a moved target.
            if self.entry is None:
                raise ValidationError("a re-entry needs a pullback entry level, not a market entry")
            if self.direction == "long" and not self.entry < self.price_at_issue:
                raise ValidationError(f"a long re-entry must wait for a pullback: entry {self.entry} is not below {self.price_at_issue}")
            if self.direction == "short" and not self.entry > self.price_at_issue:
                raise ValidationError(f"a short re-entry must wait for a bounce: entry {self.entry} is not above {self.price_at_issue}")

    def default_id(self) -> str:
        body = {k: v for k, v in self.to_dict().items() if k != "id"}
        digest = hashlib.sha256(canonical_json(body).encode()).hexdigest()[:6]
        return f"{self.ticker}-{self.issued_at[:10]}-{digest}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("reentry_of") is None:
            d.pop("reentry_of", None)  # keeps records sealed before re-entries existed hashing the same
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Prediction":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValidationError(f"unknown fields: {sorted(unknown)}")
        return cls(**data)


@dataclass
class Close:
    """An agent withdrawing earlier calls because the thesis changed.

    Sealed in the same chain as predictions, so it is timestamped and cannot be
    backdated. It applies to live calls on ``ticker`` issued *before* it, narrowed by
    ``direction``, ``agents`` or an exact ``ref`` id when given. An entered call exits
    at the next trading day's open; a call still waiting for entry is cancelled.
    """

    ticker: str
    issued_at: str = field(default_factory=utc_now_iso)
    source_agent: str = "pm"
    reason: str = ""
    direction: str | None = None
    agents: list[str] = field(default_factory=list)
    ref: str | None = None
    thesis_run: str | None = None
    kind: str = "close"
    id: str = ""

    def __post_init__(self) -> None:
        self.ticker = self.ticker.strip().upper()
        if not self.ticker:
            raise ValidationError("ticker is required")
        self.source_agent = (self.source_agent or "pm").strip().lower()
        self.issued_at = parse_iso(self.issued_at).isoformat()
        if self.direction is not None:
            self.direction = self.direction.strip().lower()
            if self.direction not in DIRECTIONS:
                raise ValidationError(f"direction must be one of {DIRECTIONS}")
        self.agents = [a.strip().lower() for a in self.agents]
        self.kind = "close"
        if not self.id:
            digest = hashlib.sha256(canonical_json({k: v for k, v in asdict(self).items() if k != "id"}).encode()).hexdigest()[:6]
            self.id = f"close-{self.ticker}-{self.issued_at[:10]}-{digest}"

    def matches(self, p: "Prediction") -> bool:
        if parse_iso(p.issued_at) >= parse_iso(self.issued_at):
            return False  # never closes a call made at the same time or later
        if self.ref:
            return p.id == self.ref
        return (p.ticker == self.ticker and (self.direction is None or p.direction == self.direction)
                and (not self.agents or p.source_agent in self.agents))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Close":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k != "action"}
        unknown = set(data) - known
        if unknown:
            raise ValidationError(f"unknown fields in close: {sorted(unknown)}")
        return cls(**data)


def is_close(data: dict[str, Any]) -> bool:
    return data.get("kind") == "close" or data.get("action") == "close"


def record_from_dict(data: dict[str, Any]) -> "Prediction | Close":
    return Close.from_dict(data) if is_close(data) else Prediction.from_dict(data)
