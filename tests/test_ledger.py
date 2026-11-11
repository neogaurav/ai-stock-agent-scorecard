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


def test_gap_through_entry_and_stop_is_minus_one_r():
    # VRT case: long, buy the dip at 227, stop 208.56; next day opens at 195.
    p = pred(price_at_issue=235.97, entry=227, stop=208.56, target=271)
    o = resolve(p, bars((195, 200, 190, 198)))
    assert o.status == "STOPPED" and o.r == -1.0


def test_trades_page(tmp_path):
    led = mk(tmp_path / "ledger", reveal_delay_days=0)
    hit, waiting = pred(ticker="AMD"), pred(ticker="APP", entry=95, stop=90, target=110)
    led.seal([hit, waiting], sealed_at="2026-10-01T15:00:00+00:00")
    led.reveal_due(now=datetime(2026, 10, 2, tzinfo=timezone.utc))
    provider = StaticProvider({"AMD": bars((100, 121, 99, 120)), "APP": bars((100, 102, 98, 101))})
    outcomes = grade_all(led.predictions(), provider, today=date(2026, 10, 3))
    out = build_site(led, outcomes, tmp_path / "site", positions={})
    trades = json.loads((out / "data" / "trades.json").read_text())
    by = {t["ticker"]: t for t in trades}
    assert by["AMD"]["group"] == "resolved" and by["AMD"]["exited_at"] == 120 and by["AMD"]["plan_rr"] == 2.0
    assert by["APP"]["group"] == "waiting" and by["APP"]["entered_on"] is None
    html = (out / "trades.html").read_text()
    assert "Resolved (1)" in html and "Waiting for entry (1)" in html


# ---------------------------------------------------------------- early close
from ai_stock_agent_scorecard import Close  # noqa: E402


def test_close_exits_open_call_at_next_open():
    p = pred()  # market long at 100, stop 90, target 120, issued 10-01
    b = bars((101, 104, 99, 103), (103, 106, 102, 105), (108, 110, 100, 101), (99, 100, 95, 96))
    c = Close(ticker="MU", reason="guide cut", issued_at="2026-10-05T20:00:00+00:00")  # Monday evening
    o = resolve(p, b, c)
    assert o.status == "CLOSED_EARLY" and o.exit_date == "2026-10-06" and o.exit_price == 108
    assert o.r == 0.8 and o.close_reason == "guide cut" and o.resolved


def test_close_does_not_override_earlier_stop_or_target():
    p = pred()
    b = bars((101, 121, 99, 119), (110, 111, 100, 101))
    o = resolve(p, b, Close(ticker="MU", issued_at="2026-10-02T21:00:00+00:00"))
    assert o.status == "TARGET_HIT" and o.closed_by is None


def test_close_cancels_untriggered_entry_and_waits_for_next_bar():
    p = pred(entry=95)
    b = bars((101, 104, 99, 103), (103, 106, 100, 105))
    late = Close(ticker="MU", issued_at="2026-10-05T20:00:00+00:00")
    assert resolve(p, b, late).status == "CLOSING"  # no bar after the close yet
    early = Close(ticker="MU", issued_at="2026-10-02T20:00:00+00:00")
    o = resolve(p, b, early)
    assert o.status == "CANCELLED" and o.r is None and not o.resolved


def test_close_matching_and_win_rate(tmp_path):
    led = mk(tmp_path)
    a = pred(source_agent="trader")
    s = pred(direction="short", stop=110, target=80, source_agent="portfolio-manager")
    other = pred(ticker="LRCX")
    led.seal([a, s, other])
    close = Close(ticker="MU", direction="long", reason="thesis broke", issued_at="2026-10-05T20:00:00+00:00")
    same_time_new = pred(issued_at="2026-10-05T20:00:00+00:00", claim="re-entry")
    led.seal([close, same_time_new])
    cm = led.close_map()
    assert set(cm) == {a.id}  # not the short, not LRCX, not the call made with the close
    assert all(p.id != close.id for p in led.predictions()) and len(led.closes()) == 1

    b = bars((101, 104, 99, 103), (103, 106, 102, 105), (96, 97, 94, 95), (99, 100, 95, 96))
    outs = grade_all(led.predictions(), StaticProvider({"MU": b, "LRCX": b}), today=date(2026, 10, 20), closes=cm)
    assert outs[a.id].status == "CLOSED_EARLY" and outs[a.id].r == -0.4
    sc = scorecard(led.predictions(), outs)
    assert sc["overall"]["early_closes"] == 1 and sc["overall"]["early_losses"] == 1
    assert sc["overall"]["win_rate"] is not None


def test_ingest_close_block(tmp_path):
    led = mk(tmp_path)
    rep = tmp_path / "runs" / "MU" / "decision.md"
    rep.parent.mkdir(parents=True)
    rep.write_text("```prediction\n" + json.dumps({"ticker": "MU", "direction": "long", "stop": 90, "target": 120,
                   "horizon_days": 10, "confidence": 0.6, "price_at_issue": 100, "source_agent": "pm"}) + "\n```\n")
    t0 = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)
    import os
    os.utime(rep, (t0.timestamp(), t0.timestamp()))
    ingest_folder(tmp_path / "runs", led, now=t0)
    rev = tmp_path / "runs" / "MU" / "review.md"
    rev.write_text('```close\n{"ticker": "MU", "source_agent": "event-reviewer", "reason": "8-K: CEO out"}\n```\n')
    t1 = t0 + timedelta(days=2)
    os.utime(rev, (t1.timestamp(), t1.timestamp()))
    ingest_folder(tmp_path / "runs", led, patterns=("*.md",), now=t1)
    assert len(led.closes()) == 1 and led.closes()[0].id.startswith("close-MU-event-reviewer-")
    assert len(led.close_map()) == 1 and led.verify().ok


def test_site_shows_closed_early(tmp_path):
    led = mk(tmp_path, reveal_delay_days=0)
    p = pred()
    led.seal([p, Close(ticker="MU", reason="thesis broke", issued_at="2026-10-05T20:00:00+00:00")])
    b = bars((101, 104, 99, 103), (103, 106, 102, 105), (108, 110, 100, 101))
    outs = grade_all(led.predictions(), StaticProvider({"MU": b}), today=date(2026, 10, 20), closes=led.close_map())
    led.reveal_due(resolved_ids={p.id})
    out = build_site(led, outs, tmp_path / "site")
    html = (out / "trades.html").read_text()
    assert "Closed early (1)" in html and "thesis broke" in html
    assert "Win rate" in (out / "index.html").read_text() and "How calls ended" in (out / "index.html").read_text()


# ---------------------------------------------------------------- re-entry
from ai_stock_agent_scorecard.extract import reentry_problem, trading_days_between  # noqa: E402
from ai_stock_agent_scorecard.score import Outcome  # noqa: E402
from ai_stock_agent_scorecard.schema import ValidationError  # noqa: E402


def test_reentry_must_wait_for_pullback():
    import pytest
    with pytest.raises(ValidationError, match="pullback entry level"):
        pred(reentry_of="MU-x")
    with pytest.raises(ValidationError, match="not below"):
        pred(reentry_of="MU-x", entry=101)
    ok = pred(reentry_of="MU-x", entry=96)
    assert ok.to_dict()["reentry_of"] == "MU-x"
    with pytest.raises(ValidationError, match="bounce"):
        pred(direction="short", stop=110, target=80, entry=99, reentry_of="MU-x")
    assert "reentry_of" not in pred().to_dict()  # old records hash exactly as before


def test_reentry_cooldown_after_stop(tmp_path):
    led = mk(tmp_path)
    first = pred()
    led.seal([first])
    stopped = {first.id: Outcome(id=first.id, status="STOPPED", exit_date="2026-10-06", exit_price=90, r=-1.0)}
    early = pred(reentry_of=first.id, entry=88, stop=80, issued_at="2026-10-08T21:00:00+00:00", price_at_issue=91)
    assert "wait 5 trading days" in reentry_problem(early, led, stopped)
    late = pred(reentry_of=first.id, entry=88, stop=80, issued_at="2026-10-13T21:00:00+00:00", price_at_issue=91)
    assert reentry_problem(late, led, stopped) is None
    target = {first.id: Outcome(id=first.id, status="TARGET_HIT", exit_date="2026-10-06", exit_price=120, r=2.0)}
    assert reentry_problem(early, led, target) is None  # no cooldown after a target hit
    assert "has not exited" in reentry_problem(early, led, {})
    assert "unknown call" in reentry_problem(pred(reentry_of="nope", entry=96), led, stopped)
    assert trading_days_between("2026-10-09", "2026-10-12") == 1  # Fri -> Mon


def test_ingest_refuses_early_reentry(tmp_path):
    import os
    led = mk(tmp_path)
    first = pred()
    led.seal([first])
    outs = {first.id: Outcome(id=first.id, status="STOPPED", exit_date="2026-10-06", exit_price=90, r=-1.0)}
    rep = tmp_path / "runs" / "MU" / "decision.md"
    rep.parent.mkdir(parents=True)
    rep.write_text("```prediction\n" + json.dumps({"ticker": "MU", "direction": "long", "entry": 86, "stop": 80, "target": 110,
                   "horizon_days": 20, "confidence": 0.5, "price_at_issue": 91, "reentry_of": first.id}) + "\n```\n")
    t = datetime(2026, 10, 7, 21, tzinfo=timezone.utc)
    os.utime(rep, (t.timestamp(), t.timestamp()))
    summary = ingest_folder(tmp_path / "runs", led, now=t, outcomes=outs)
    assert summary["sealed"] == 0 and "re-entries wait 5 trading days" in summary["errors"][0]


def test_exits_command_and_reentry_tab(tmp_path, capsys):
    from ai_stock_agent_scorecard.cli import main
    led = mk(tmp_path, reveal_delay_days=0)
    first = pred()
    led.seal([first])
    today = date.today().isoformat()
    (led.private / "outcomes.json").write_text(json.dumps({first.id: Outcome(
        id=first.id, status="STOPPED", fill_price=100, fill_date=today, exit_date=today, exit_price=90, r=-1.0).to_dict()}))
    state = tmp_path / "seen.json"
    main(["--root", str(tmp_path), "exits", "--state", str(state)])
    rows = json.loads(capsys.readouterr().out)["exits"]
    assert rows[0]["id"] == first.id and rows[0]["status"] == "STOPPED" and rows[0]["reentry_allowed_from"] > today
    main(["--root", str(tmp_path), "exits", "--state", str(state)])
    assert json.loads(capsys.readouterr().out)["exits"] == []  # reported once

    re = pred(reentry_of=first.id, entry=88, stop=80, price_at_issue=91, issued_at="2026-10-20T21:00:00+00:00")
    led.seal([re])
    led.reveal_due(resolved_ids={first.id, re.id})
    html = (build_site(led, {}, tmp_path / "site") / "trades.html").read_text()
    assert "Re-entries (1)" in html
