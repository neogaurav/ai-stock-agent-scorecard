"""Grade predictions against daily bars and roll results into a scorecard.

Grading is deliberately conservative:
* grading starts on the first trading day *after* the call was issued;
* if one bar touches both stop and target, the stop is assumed to hit first;
* gaps through a level fill at the open (so a gap through the stop is worse than -1R);
* on the bar where a limit entry triggers, only the stop is checked;
* a call withdrawn by a sealed ``close`` exits at the open of the first trading day
  after the close (never at a price the agent picks); if it was never entered it is
  cancelled and takes no R.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from statistics import mean
from typing import Any, Iterable

from .prices import Bar, PriceProvider
from .schema import Close, Prediction, parse_iso

TARGET_HIT = "TARGET_HIT"
STOPPED = "STOPPED"
EXPIRED = "EXPIRED"
NOT_TRIGGERED = "NOT_TRIGGERED"
OPEN = "OPEN"
PENDING_ENTRY = "PENDING_ENTRY"
CLOSED_EARLY = "CLOSED_EARLY"   # entered, then withdrawn by the agent: exits at the next open
CANCELLED = "CANCELLED"         # withdrawn before the entry ever filled: no R
CLOSING = "CLOSING"             # withdrawn, waiting for the next trading day's open
RESOLVED = (TARGET_HIT, STOPPED, EXPIRED, CLOSED_EARLY)


@dataclass
class Outcome:
    id: str
    status: str
    r: float | None = None
    fill_price: float | None = None
    fill_date: str | None = None
    exit_price: float | None = None
    exit_date: str | None = None
    bars_used: int = 0
    closed_by: str | None = None
    close_reason: str | None = None
    closed_on: str | None = None

    @property
    def resolved(self) -> bool:
        return self.status in RESOLVED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _r(p: Prediction, fill: float, exit_: float) -> float:
    risk = abs(fill - p.stop) or 1e-9
    move = exit_ - fill if p.direction == "long" else fill - exit_
    return round(move / risk, 3)


def resolve(p: Prediction, bars: list[Bar], close: Close | None = None) -> Outcome:
    issued = parse_iso(p.issued_at).date()
    window = [b for b in sorted(bars, key=lambda b: b.day) if b.day > issued][: p.horizon_days]
    if close is None:
        return _walk(p, window)
    close_day = parse_iso(close.issued_at).date()
    k = next((i for i, b in enumerate(window) if b.day > close_day), None)
    out = _walk(p, window if k is None else window[:k])
    if out.status not in (OPEN, PENDING_ENTRY):
        return out  # stop, target or horizon came first; the close changes nothing
    out.closed_by, out.close_reason, out.closed_on = close.id, close.reason, close.issued_at[:10]
    out.bars_used = len(window)
    if k is None:
        out.status = CLOSING
        return out
    b = window[k]
    if out.status == PENDING_ENTRY:
        out.status = CANCELLED
        return out
    return _exit(out, p, out.fill_price, CLOSED_EARLY, b.open, b)


def _walk(p: Prediction, window: list[Bar]) -> Outcome:
    long = p.direction == "long"
    out = Outcome(id=p.id, status=PENDING_ENTRY if p.entry is not None else OPEN, bars_used=len(window))

    fill: float | None = None
    start_idx = 0
    if p.entry is None:
        fill = p.price_at_issue
        out.fill_price, out.fill_date = fill, p.issued_at[:10]
    else:
        pullback = (p.entry < p.price_at_issue) if long else (p.entry > p.price_at_issue)
        for i, b in enumerate(window):
            if long:
                hit = b.low <= p.entry if pullback else b.high >= p.entry
                price = min(b.open, p.entry) if pullback else max(b.open, p.entry)
            else:
                hit = b.high >= p.entry if pullback else b.low <= p.entry
                price = max(b.open, p.entry) if pullback else min(b.open, p.entry)
            if hit:
                fill = price
                out.fill_price, out.fill_date, out.status = fill, b.day.isoformat(), OPEN
                # Gapped through the entry AND the stop (e.g. a buy-the-dip order filled
                # far below the stop): the thesis was invalidated on arrival. Count a
                # full -1R loss instead of letting the stop "exit" sit above the fill.
                beyond_stop = fill <= p.stop if long else fill >= p.stop
                if beyond_stop:
                    out.status, out.exit_price, out.exit_date = STOPPED, round(fill, 4), b.day.isoformat()
                    out.r = -1.0
                    return out
                # trigger bar: stop only
                stop_hit = b.low <= p.stop if long else b.high >= p.stop
                if stop_hit:
                    out.status, out.exit_price, out.exit_date = STOPPED, p.stop, b.day.isoformat()
                    out.r = _r(p, fill, p.stop)
                    return out
                start_idx = i + 1
                break
        if fill is None:
            if len(window) >= p.horizon_days:
                out.status = NOT_TRIGGERED
            return out

    for b in window[start_idx:]:
        if long:
            if b.open <= p.stop:
                return _exit(out, p, fill, STOPPED, b.open, b)
            if b.open >= p.target:
                return _exit(out, p, fill, TARGET_HIT, b.open, b)
            if b.low <= p.stop:
                return _exit(out, p, fill, STOPPED, p.stop, b)
            if b.high >= p.target:
                return _exit(out, p, fill, TARGET_HIT, p.target, b)
        else:
            if b.open >= p.stop:
                return _exit(out, p, fill, STOPPED, b.open, b)
            if b.open <= p.target:
                return _exit(out, p, fill, TARGET_HIT, b.open, b)
            if b.high >= p.stop:
                return _exit(out, p, fill, STOPPED, p.stop, b)
            if b.low <= p.target:
                return _exit(out, p, fill, TARGET_HIT, p.target, b)

    if len(window) >= p.horizon_days and window:
        last = window[-1]
        return _exit(out, p, fill, EXPIRED, last.close, last)
    return out


def _exit(out: Outcome, p: Prediction, fill: float, status: str, price: float, bar: Bar) -> Outcome:
    out.status, out.exit_price, out.exit_date = status, round(price, 4), bar.day.isoformat()
    out.r = _r(p, fill, price)
    return out


def grade_all(preds: Iterable[Prediction], provider: PriceProvider, today: date | None = None,
              closes: dict[str, Close] | None = None) -> dict[str, Outcome]:
    today = today or date.today()
    closes = closes or {}
    results: dict[str, Outcome] = {}
    for p in preds:
        start = parse_iso(p.issued_at).date()
        end = min(today, start + timedelta(days=int(p.horizon_days * 1.6) + 10))
        results[p.id] = resolve(p, provider.daily_bars(p.ticker, start, end), closes.get(p.id))
    return results


# ---------------------------------------------------------------- scorecard
BUCKETS = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]


def _bucket_label(lo: float, hi: float) -> str:
    if lo == 0.0:
        return "under 50%"
    if hi > 1:
        return "80%+"
    return f"{int(lo * 100)}–{int(hi * 100)}%"


def _is_win(o: Outcome) -> bool:
    return o.status == TARGET_HIT or (o.r is not None and o.r > 0)


def _stats(rows: list[tuple[Prediction, Outcome]]) -> dict[str, Any]:
    """Win rate counts every entered call that finished, early closes included (a reader may
    have taken the trade). Target-hit rate leaves early closes out, since they never got to
    find out whether stop or target came first."""
    res = [(p, o) for p, o in rows if o.resolved]
    rs = [o.r for _, o in res if o.r is not None]
    wins = sum(1 for _, o in res if _is_win(o))
    played_out = [o for _, o in res if o.status != CLOSED_EARLY]
    hits = sum(1 for o in played_out if o.status == TARGET_HIT)
    early = [o for _, o in res if o.status == CLOSED_EARLY]
    early_r = [o.r for o in early if o.r is not None]
    return {
        "n": len(rows),
        "resolved": len(res),
        "win_rate": round(wins / len(res), 3) if res else None,
        "hit_rate": round(hits / len(played_out), 3) if played_out else None,
        "avg_r": round(mean(rs), 3) if rs else None,
        "total_r": round(sum(rs), 2) if rs else 0.0,
        "early_closes": len(early),
        "early_wins": sum(1 for o in early if _is_win(o)),
        "early_losses": sum(1 for o in early if not _is_win(o)),
        "early_avg_r": round(mean(early_r), 3) if early_r else None,
        "cancelled": sum(1 for _, o in rows if o.status == CANCELLED),
        "reentries": sum(1 for p, _ in rows if p.reentry_of),
        "reentries_resolved": sum(1 for p, _ in res if p.reentry_of),
        "reentry_avg_r": (round(mean(rr), 3) if (rr := [o.r for p, o in res if p.reentry_of and o.r is not None]) else None),
    }


def scorecard(preds: list[Prediction], outcomes: dict[str, Outcome], *, min_n: int = 8) -> dict[str, Any]:
    rows = [(p, outcomes[p.id]) for p in preds if p.id in outcomes]
    resolved = [(p, o) for p, o in rows if o.resolved]
    # Confidence is "odds the target comes before the stop"; an early close never found out.
    played_out = [(p, o) for p, o in resolved if o.status != CLOSED_EARLY]
    counts: dict[str, int] = defaultdict(int)
    for _, o in rows:
        counts[o.status] += 1

    overall = _stats(rows)
    brier = (round(mean((p.confidence - (1.0 if o.status == TARGET_HIT else 0.0)) ** 2 for p, o in played_out), 4)
             if played_out else None)

    by_agent: dict[str, list] = defaultdict(list)
    for p, o in rows:
        by_agent[p.source_agent].append((p, o))
    agents = sorted(({"agent": a, **_stats(v)} for a, v in by_agent.items()),
                    key=lambda s: (s["avg_r"] is None, -(s["avg_r"] or 0)))

    calibration = []
    for lo, hi in BUCKETS:
        b = [(p, o) for p, o in played_out if lo <= p.confidence < hi]
        if not b:
            continue
        stated = mean(p.confidence for p, _ in b)
        actual = sum(1 for _, o in b if o.status == TARGET_HIT) / len(b)
        gap = stated - actual
        verdict = ("calibrated" if abs(gap) < 0.05 else "close" if abs(gap) < 0.10
                   else "overconfident" if gap > 0 else "underconfident")
        if len(b) < min_n:
            verdict = "too few"
        calibration.append({"bucket": _bucket_label(lo, hi), "n": len(b), "stated": round(stated, 3),
                            "actual": round(actual, 3), "verdict": verdict})

    insights = _insights(agents, calibration, by_agent, min_n)
    recent = sorted(rows, key=lambda po: po[0].issued_at, reverse=True)[:25]
    return {
        "totals": {"predictions": len(rows), "resolved": len(resolved), **dict(counts)},
        "overall": {**overall, "brier": brier},
        "agents": agents,
        "calibration": calibration,
        "insights": insights,
        "recent": [{**_public_row(p), **o.to_dict()} for p, o in recent],
    }


def _public_row(p: Prediction) -> dict[str, Any]:
    return {"ticker": p.ticker, "direction": p.direction, "agent": p.source_agent, "issued_at": p.issued_at,
            "entry": p.entry_ref, "stop": p.stop, "target": p.target, "confidence": p.confidence,
            "horizon_days": p.horizon_days, "claim": p.claim}


def _insights(agents, calibration, by_agent, min_n) -> list[str]:
    out = []
    ranked = [a for a in agents if a["resolved"] >= min_n and a["avg_r"] is not None]
    if ranked:
        best = ranked[0]
        out.append(f"{best['agent']} leads with {best['avg_r']:+.2f}R per call over {best['resolved']} resolved calls.")
        for a in ranked:
            if a["avg_r"] < 0:
                out.append(f"{a['agent']} is net negative ({a['avg_r']:+.2f}R per call); weigh it less or change its horizon.")
    over = [c for c in calibration if c["verdict"] == "overconfident"]
    if len(over) == 1:
        c = over[0]
        out.append(f"Calls stated at {c['bucket']} confidence hit only {c['actual']:.0%} of the time.")
    elif over:
        worst = max(over, key=lambda c: c["stated"] - c["actual"])
        out.append(f"Stated confidence runs high in {len(over)} of {len(calibration)} buckets; the worst is "
                   f"{worst['bucket']}, which hit only {worst['actual']:.0%}. Treat agent confidence as a ranking, not a probability.")
    for agent, rows in by_agent.items():
        short = [(p, o) for p, o in rows if p.horizon_days < 30]
        longh = [(p, o) for p, o in rows if p.horizon_days >= 30]
        s, l = _stats(short), _stats(longh)
        if s["resolved"] >= min_n and l["resolved"] >= min_n and s["avg_r"] is not None and l["avg_r"] is not None:
            if s["avg_r"] < 0 < l["avg_r"]:
                out.append(f"{agent} loses on horizons under 30 days ({s['avg_r']:+.2f}R) but wins at 30+ ({l['avg_r']:+.2f}R).")
    for a in agents:
        if a.get("early_closes", 0) >= 3 and a.get("early_avg_r") is not None:
            out.append(f"{a['agent']} closed {a['early_closes']} calls early ({a['early_wins']} up, {a['early_losses']} down, "
                       f"{a['early_avg_r']:+.2f}R on average).")
    for a in agents:
        if a.get("reentries_resolved", 0) >= 3 and a.get("reentry_avg_r") is not None:
            out.append(f"{a['agent']}'s {a['reentries_resolved']} resolved re-entries average {a['reentry_avg_r']:+.2f}R "
                       f"(all its calls: {a['avg_r']:+.2f}R).")
    human = next((a for a in agents if a["agent"] == "human"), None)
    if human and human["avg_r"] is not None and ranked:
        ai = [a for a in ranked if a["agent"] != "human"]
        if ai:
            out.append(f"Human calls average {human['avg_r']:+.2f}R vs best agent {ai[0]['agent']} at {ai[0]['avg_r']:+.2f}R.")
    return out
