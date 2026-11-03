import json
from datetime import date, datetime, timedelta, timezone

import pytest

from ai_stock_agent_scorecard import Ledger, Prediction, ValidationError, resolve, scorecard
from ai_stock_agent_scorecard.extract import extract_blocks, ingest_folder
from ai_stock_agent_scorecard.prices import Bar, StaticProvider
from ai_stock_agent_scorecard.score import grade_all
from ai_stock_agent_scorecard.site import build_site


def mk(path, **cfg):
    """Ledger with the hindsight guard off, for tests that seal historical calls."""
    led = Ledger(path)
    led.init(max_seal_lag_minutes=0, **cfg)
    return led


def pred(**kw):
    base = dict(ticker="MU", direction="long", stop=90, target=120, horizon_days=10, confidence=0.6,
                price_at_issue=100, issued_at="2026-10-01T14:00:00+00:00", source_agent="technicals")
    base.update(kw)
    return Prediction(**base)


def bars(*rows, start=date(2026, 10, 2)):
    out, d = [], start
    for o, h, l, c in rows:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(Bar(d, o, h, l, c))
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------- schema
def test_validation_rejects_bad_levels():
    with pytest.raises(ValidationError):
        pred(stop=110)  # stop above entry for a long
    with pytest.raises(ValidationError):
        pred(direction="short")  # short needs target < entry < stop
    assert pred(confidence=65).confidence == 0.65


# ---------------------------------------------------------------- chain
def test_seal_verify_and_tamper(tmp_path):
    led = mk(tmp_path)
    led.seal([pred(), pred(ticker="LRCX")])
    assert led.verify().ok and len(led.chain()) == 2
    led.seal([pred()])  # idempotent
    assert len(led.chain()) == 2

    lines = led.chain_path.read_text().splitlines()
    first = json.loads(lines[0])
    first["sealed_at"] = "2026-09-01T00:00:00+00:00"  # backdate
    lines[0] = json.dumps(first)
    led.chain_path.write_text("\n".join(lines) + "\n")
    rep = led.verify()
    assert not rep.ok and "hash mismatch" in rep.errors[0]


def test_commit_reveal(tmp_path):
    led = mk(tmp_path, reveal_delay_days=5)
    sealed = datetime(2026, 10, 1, 15, tzinfo=timezone.utc)
    led.seal([pred(), pred(ticker="VST", issued_at="2026-10-01T14:30:00+00:00")], sealed_at=sealed.isoformat())
    assert "MU" not in led.chain_path.read_text()  # public chain leaks nothing
    assert led.reveal_due(now=sealed + timedelta(days=2)) == []
    early = led.reveal_due(now=sealed + timedelta(days=2), resolved_ids={led.predictions()[0].id})
    assert len(early) == 1
    assert len(led.reveal_due(now=sealed + timedelta(days=6))) == 1
    assert led.verify().ok and led.verify().reveals_checked == 2

    rows = led.reveals_path.read_text().splitlines()
    r = json.loads(rows[0])
    r["record"]["target"] = 200.0
    r["record_json"] = json.dumps(r["record"], sort_keys=True, separators=(",", ":"))
    rows[0] = json.dumps(r)
    led.reveals_path.write_text("\n".join(rows) + "\n")
    assert not led.verify().ok


def test_cannot_seal_future_dated_call(tmp_path):
    with pytest.raises(ValueError):
        Ledger(tmp_path).seal([pred(issued_at="2026-12-01T00:00:00+00:00")], sealed_at="2026-10-01T00:00:00+00:00")


# ---------------------------------------------------------------- grading
def test_target_hit():
    o = resolve(pred(), bars((100, 105, 99, 104), (104, 121, 103, 119)))
    assert o.status == "TARGET_HIT" and o.r == 2.0


def test_same_bar_stop_wins():
    o = resolve(pred(), bars((100, 125, 85, 110)))
    assert o.status == "STOPPED" and o.r == -1.0


def test_gap_through_stop_is_worse_than_1r():
    o = resolve(pred(), bars((80, 82, 78, 81)))
    assert o.status == "STOPPED" and o.r == -2.0


def test_expired_and_open():
    flat = [(100, 101, 99, 103)] * 10
    o = resolve(pred(), bars(*flat))
    assert o.status == "EXPIRED" and o.r == 0.3
    assert resolve(pred(), bars(*flat[:4])).status == "OPEN"


def test_limit_entry_triggers_and_not_triggered():
    p = pred(entry=95, stop=90, target=110)
    o = resolve(p, bars((100, 101, 94, 96), (96, 111, 95, 110)))
    assert o.status == "TARGET_HIT" and o.fill_price == 95 and o.r == 3.0
    never = resolve(p, bars(*[(100, 102, 98, 100)] * 10))
    assert never.status == "NOT_TRIGGERED" and never.r is None


def test_short():
    p = pred(direction="short", stop=110, target=80)
    assert resolve(p, bars((100, 101, 79, 80))).status == "TARGET_HIT"


def test_grading_ignores_issue_day_bar():
    o = resolve(pred(), bars((100, 130, 99, 125), start=date(2026, 10, 1)))
    assert o.status == "OPEN"  # the 2026-10-01 bar happened partly before the call


def test_scorecard_and_calibration():
    preds, outs = [], {}
    for i in range(10):
        p = pred(ticker=f"T{i}", confidence=0.85, source_agent="fundamentals")
        preds.append(p)
        outs[p.id] = resolve(p, bars((100, 101, 85, 88)))  # all stopped
    for i in range(10):
        p = pred(ticker=f"U{i}", confidence=0.55, source_agent="options-flow")
        preds.append(p)
        outs[p.id] = resolve(p, bars((100, 125, 99, 120)) if i < 6 else bars((100, 101, 85, 88)))
    sc = scorecard(preds, outs)
    assert sc["agents"][0]["agent"] == "options-flow"
    cal = {c["bucket"]: c for c in sc["calibration"]}
    assert cal["80%+"]["verdict"] == "overconfident"
    assert any("net negative" in s for s in sc["insights"])


# ---------------------------------------------------------------- extraction + site
REPORT = """# MU thesis
Some prose the parser ignores.
```prediction
[{"ticker": "MU", "direction": "long", "entry": 95, "stop": 90, "target": 110, "horizon_days": 10,
  "confidence": 0.65, "price_at_issue": 100, "source_agent": "technicals", "claim": "Pullback to support"},
 {"ticker": "MU", "direction": "long", "stop": 120, "target": 110, "horizon_days": 5,
  "confidence": 0.5, "price_at_issue": 100}]
```
"""


def test_extract_blocks_reports_bad_rows():
    preds, errors = extract_blocks(REPORT)
    assert len(preds) == 1 and len(errors) == 1


def test_ingest_is_idempotent(tmp_path):
    reports = tmp_path / "results"
    reports.mkdir()
    (reports / "MU.md").write_text(REPORT)
    led = Ledger(tmp_path / "ledger")
    assert ingest_folder(reports, led)["sealed"] == 1
    assert ingest_folder(reports, led)["files"] == 0


def test_site_only_shows_revealed(tmp_path):
    led = mk(tmp_path / "ledger", reveal_delay_days=0, position_badges=True)
    p1, p2 = pred(claim="revealed one"), pred(ticker="ARRY", claim="secret one")
    led.seal([p1], sealed_at="2026-10-01T15:00:00+00:00")
    led.reveal_due(now=datetime(2026, 10, 2, tzinfo=timezone.utc))
    led.seal([p2], sealed_at=datetime.now(timezone.utc).isoformat())
    led.config_path.write_text(json.dumps({**led.config, "reveal_delay_days": 5}))
    provider = StaticProvider({"MU": bars((100, 121, 99, 120)), "ARRY": bars((100, 101, 99, 100))})
    outcomes = grade_all(led.predictions(), provider, today=date(2026, 10, 20))
    out = build_site(led, outcomes, tmp_path / "site", positions={"MU": {"side": "long", "qty": 50}})
    html = (out / "index.html").read_text()
    assert "revealed one" in html and "secret one" not in html
    assert "Author: LONG 50 sh" in html and "Chain verified" in html
    assert (out / "verify.html").exists() and (out / "data" / "chain.jsonl").read_text().count("\n") == 2


def test_empty_ledger_builds_site(tmp_path):
    led = mk(tmp_path / "ledger")
    out = build_site(led, {}, tmp_path / "site", positions={})
    assert "Starting soon" in (out / "index.html").read_text()
    led.seal([pred()])
    out = build_site(led, {}, tmp_path / "site", positions={})
    assert "Sealed, awaiting reveal (1)" in (out / "index.html").read_text()


def test_late_seal_is_refused(tmp_path):
    led = Ledger(tmp_path)
    led.init()  # default guard: 120 minutes
    with pytest.raises(ValueError, match="hindsight"):
        led.seal([pred()], sealed_at="2026-10-01T18:00:00+00:00")
    assert len(led.seal([pred()], sealed_at="2026-10-01T15:00:00+00:00")) == 1


def test_ingest_refuses_old_reports_and_rewrites(tmp_path):
    import os
    reports = tmp_path / "results" / "MU" / "2026-10-22" / "5_portfolio"
    reports.mkdir(parents=True)
    old = reports / "decision.md"
    old.write_text(REPORT)
    os.utime(old, (1_700_000_000, 1_700_000_000))  # a 2023 file: must not be backfilled
    led = Ledger(tmp_path / "ledger")
    led.init()
    s = ingest_folder(tmp_path / "results", led, patterns=("decision.md",))
    assert s["sealed"] == 0 and s["too_old"] == 1

    new = tmp_path / "results" / "MU" / "2026-11-03" / "5_portfolio"
    new.mkdir(parents=True)
    (new / "decision.md").write_text(REPORT)
    assert ingest_folder(tmp_path / "results", led, patterns=("decision.md",))["sealed"] == 1
    (new / "decision.md").write_text(REPORT.replace("Pullback to support", "rewritten later"))
    assert ingest_folder(tmp_path / "results", led, patterns=("decision.md",))["sealed"] == 0
    assert len(led.chain()) == 1


def test_blanket_disclosure_by_default(tmp_path):
    led = mk(tmp_path / "ledger", reveal_delay_days=0)
    led.seal([pred()], sealed_at="2026-10-01T15:00:00+00:00")
    led.reveal_due(now=datetime(2026, 10, 2, tzinfo=timezone.utc))
    html = (build_site(led, {}, tmp_path / "site") / "index.html").read_text()
    assert "Author:" not in html and "long or short positions in any security" in html
