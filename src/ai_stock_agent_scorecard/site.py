"""Build the public static site: scorecard, verify page and raw public data.

Only *revealed* predictions feed the public scorecard, so every number on the
site can be recomputed by a visitor from the files in ``/data``.
"""
from __future__ import annotations

import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ledger import Ledger
from .schema import Prediction, is_close
from .score import Outcome, scorecard

DISCLAIMER = (
    "<p><strong>For educational and research purposes only.</strong> All predictions on this site are generated "
    "automatically by AI agents and are published to evaluate how accurate those agents are. They are not investment "
    "advice, recommendations, or solicitations to buy or sell any security, and they do not take into account your "
    "financial situation or objectives.</p>"
    "<p><strong>Trading involves substantial risk of loss.</strong> Past accuracy, including the scores shown here, does "
    "not guarantee future results. Predictions may be wrong, outdated, or based on incomplete data. Do your own research "
    "and consult a licensed financial professional before making investment decisions.</p>"
    "<p><strong>Author positions.</strong> The author may hold, buy or sell long or short positions in any security on "
    "this site at any time, including before and after a call is published. Calls are generated automatically by AI "
    "agents and are not the author's personal recommendations.</p>"
    "<p>This site is free, is not a subscription service, and does not provide personalized advice. No compensation is "
    "received from any company discussed.</p>"
)

CSS = """
:root{--paper:#F2F4F6;--surface:#FFFFFF;--ink:#14213D;--muted:#5B6577;--line:#DCE1E8;--soft:#E8ECF1;
--gain:#0E7C66;--gainbg:#DDF1EB;--loss:#B4232A;--lossbg:#F9E3E4;--amber:#A86A0C;--amberbg:#FBEFD9;--sky:#2A5D9F;--skybg:#E1EAF6;--flat:#8A93A3;
--sans:'IBM Plex Sans',system-ui,-apple-system,'Segoe UI',sans-serif;--cond:'IBM Plex Sans Condensed','IBM Plex Sans',system-ui,sans-serif}
@media (prefers-color-scheme:dark){:root{--paper:#0E1729;--surface:#14203A;--ink:#E7ECF4;--muted:#9AA6BA;--line:#25324D;--soft:#1B2942;
--gain:#4CC7A5;--gainbg:#0F3A33;--loss:#F08A8F;--lossbg:#41191F;--amber:#E6A94A;--amberbg:#3B2A0E;--sky:#8DB4EA;--skybg:#16304F;--flat:#6E7A90}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:400 15px/1.55 var(--sans);font-variant-numeric:tabular-nums}
a{color:var(--sky);text-underline-offset:3px}a:focus-visible,button:focus-visible{outline:2px solid var(--sky);outline-offset:2px;border-radius:4px}
.wrap{max-width:1120px;margin:0 auto;padding:0 28px}
.top{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:22px 0 18px;border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:12px;color:var(--ink);text-decoration:none}
.mark{width:30px;height:30px;flex:none}.brand b{font-weight:600;font-size:16px;display:block;line-height:1.2}.brand small{color:var(--muted);font-size:12.5px}
nav{display:flex;gap:4px;flex-wrap:wrap}nav a{color:var(--muted);text-decoration:none;font-size:14px;padding:6px 12px;border-radius:999px}
nav a:hover{color:var(--ink);background:var(--soft)}nav a[aria-current=page]{color:var(--ink);background:var(--surface);box-shadow:inset 0 0 0 1px var(--line)}
.notice{margin:20px 0 0;padding:10px 14px;border-radius:8px;background:var(--amberbg);color:var(--amber);font-size:13.5px}
.hero{padding:44px 0 8px}
.hero h1{font:600 clamp(30px,4.6vw,50px)/1.08 var(--cond);letter-spacing:-.01em;margin:0;max-width:20ch}
.hero p.lede{color:var(--muted);font-size:16px;margin:12px 0 0;max-width:62ch}
.curve{margin:28px 0 0;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 18px 10px}
.curve svg{display:block;width:100%;height:auto;overflow:visible}
.curve .axis{stroke:var(--line)}.curve .zero{stroke:var(--flat);stroke-dasharray:3 4}.curve .line{fill:none;stroke:var(--ink);stroke-width:2}
.curve .area{fill:var(--gain);opacity:.08}.curve .area.neg{fill:var(--loss)}.curve text{fill:var(--muted);font:12px var(--sans)}
.curve .end{fill:var(--ink);font:600 14px var(--sans)}
.curve circle{stroke:var(--surface);stroke-width:2}.curve circle.w{fill:var(--gain)}.curve circle.l{fill:var(--loss)}.curve circle.f{fill:var(--flat)}
.strip{display:grid;grid-template-columns:repeat(6,1fr);margin:22px 0 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.strip div{padding:14px 16px;border-left:1px solid var(--line)}.strip div:first-child{border-left:0;padding-left:0}
.strip dt{font-size:12.5px;color:var(--muted)}.strip dd{margin:2px 0 0;font:600 26px/1.15 var(--cond)}
.pos{color:var(--gain)}.neg{color:var(--loss)}
section.s{display:grid;grid-template-columns:260px 1fr;gap:40px;padding:40px 0;border-bottom:1px solid var(--line)}
section.s>header h2{font:600 20px/1.25 var(--cond);margin:0}section.s>header p{color:var(--muted);font-size:13.5px;margin:8px 0 0}
table{width:100%;border-collapse:collapse;font-size:14px}th{font-weight:500;color:var(--muted);font-size:12.5px;text-align:left;padding:0 10px 8px;border-bottom:1px solid var(--line)}
td{padding:11px 10px;border-bottom:1px solid var(--line);vertical-align:middle}tr:last-child td{border-bottom:0}
th:first-child,td:first-child{padding-left:0}td.r,th.r{text-align:right}
.div{position:relative;height:10px;min-width:140px}.div:before{content:'';position:absolute;left:50%;top:-3px;bottom:-3px;border-left:1px solid var(--flat)}
.div i{position:absolute;top:0;bottom:0;border-radius:3px}
.stack{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--soft)}.stack i{display:block;height:100%}
.legend{display:flex;flex-wrap:wrap;gap:8px 28px;margin:16px 0 0;padding:0;list-style:none}
.legend li{display:flex;align-items:baseline;gap:8px;font-size:13.5px;color:var(--muted)}.legend b{font:600 22px var(--cond);color:var(--ink)}
.sw{width:10px;height:10px;border-radius:2px;display:inline-block;align-self:center}
.note{color:var(--muted);font-size:13px;margin:16px 0 0;max-width:72ch}
.pill{font-size:12px;font-weight:500;padding:2px 9px;border-radius:999px;white-space:nowrap;display:inline-block}
.good{background:var(--gainbg);color:var(--gain)}.bad{background:var(--lossbg);color:var(--loss)}.info{background:var(--skybg);color:var(--sky)}
.warn{background:var(--amberbg);color:var(--amber)}.neutral{background:var(--soft);color:var(--muted)}
.tk{font-weight:600}.dir{font-size:11.5px;font-weight:600;margin-left:6px;padding:1px 7px;border-radius:999px;color:var(--muted);background:var(--soft)}.dir.long{color:var(--sky);background:var(--skybg)}.dir.short{color:var(--amber);background:var(--amberbg)}.sub{color:var(--muted);font-size:12.5px}
.claim{color:var(--muted);font-size:13px}
.ins{margin:0;padding:0;list-style:none}.ins li{padding:10px 0 10px 16px;border-bottom:1px solid var(--line);position:relative}
.ins li:before{content:'';position:absolute;left:0;top:19px;width:6px;height:6px;border-radius:50%;background:var(--sky)}.ins li:last-child{border-bottom:0}
.proof{display:flex;flex-wrap:wrap;gap:10px 24px;align-items:center;font-size:14px}
code{font:12.5px 'IBM Plex Mono',ui-monospace,monospace;background:var(--soft);padding:2px 6px;border-radius:4px;word-break:break-all}
details{margin-top:18px}summary{cursor:pointer;color:var(--sky);font-size:14px}
.empty{padding:28px;border:1px dashed var(--line);border-radius:12px;color:var(--muted)}
footer{padding:36px 0 56px;color:var(--muted);font-size:12.5px;max-width:84ch}footer p{margin:0 0 10px}
textarea{width:100%;min-height:120px;font:12.5px 'IBM Plex Mono',ui-monospace,monospace;background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:10px}
button{font:500 14px var(--sans);padding:8px 16px;border-radius:8px;border:1px solid var(--ink);background:var(--ink);color:var(--paper);cursor:pointer}
pre{white-space:pre-wrap}
@media (max-width:900px){section.s{grid-template-columns:1fr;gap:16px;padding:30px 0}.strip{grid-template-columns:repeat(3,1fr)}
.strip div:nth-child(4){border-left:0;padding-left:0}.strip div:nth-child(n+4){border-top:1px solid var(--line)}}
@media (max-width:560px){.wrap{padding:0 18px}.strip{grid-template-columns:repeat(2,1fr)}.strip div{border-left:0!important;padding-left:0!important}
.hide-s{display:none}.curve{overflow-x:auto;padding:12px}.curve svg{min-width:620px}}
"""

FONTS = ("<link rel='preconnect' href='https://fonts.googleapis.com'><link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
         "<link href='https://fonts.googleapis.com/css2?family=IBM+Plex+Mono&family=IBM+Plex+Sans+Condensed:wght@600&"
         "family=IBM+Plex+Sans:wght@400;500;600&display=swap' rel='stylesheet'>")
MARK = ("<svg class='mark' viewBox='0 0 30 30' aria-hidden='true'><rect width='30' height='30' rx='8' fill='var(--ink)'/>"
        "<polyline points='6,20 11,16 15,18 19,11 24,8' fill='none' stroke='var(--paper)' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'/>"
        "<circle cx='24' cy='8' r='2.4' fill='var(--gain)'/></svg>")

STATUS_PILL = {
    "TARGET_HIT": ("good", "Target"), "STOPPED": ("bad", "Stopped"), "EXPIRED": ("neutral", "Expired"),
    "OPEN": ("info", "Open"), "PENDING_ENTRY": ("info", "Waiting for entry"), "NOT_TRIGGERED": ("neutral", "Not triggered"),
    "CLOSED_EARLY": ("neutral", "Closed early"), "CANCELLED": ("neutral", "Cancelled"), "CLOSING": ("warn", "Closing at next open"),
}
VERDICT_PILL = {"calibrated": "good", "close": "good", "overconfident": "bad", "underconfident": "warn", "too few": "neutral"}


def e(x: Any) -> str:
    return html.escape(str(x))


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.0%}"


def _r(x: float | None) -> str:
    return "–" if x is None else f"{x:+.2f}R"


def load_positions(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text("utf-8"))
    return {k.upper(): v for k, v in data.get("positions", data).items()}


def position_badge(ticker: str, positions: dict[str, dict[str, Any]], as_of: str) -> str:
    pos = positions.get(ticker.upper())
    if not pos or not pos.get("qty"):
        return f'<span class="pill neutral" title="Author position as of {e(as_of)}">Author: none</span>'
    side = pos.get("side", "long").upper()
    qty = pos.get("qty")
    unit = pos.get("unit", "sh")
    return f'<span class="pill warn" title="Author position as of {e(pos.get("as_of", as_of))}">Author: {e(side)} {e(qty)} {e(unit)}</span>'


def _page(title: str, body: str, cfg: dict[str, Any], current: str = "index") -> str:
    links = [("index", "index.html", "Scorecard"), ("trades", "trades.html", "Trades"), ("verify", "verify.html", "Verify"),
             ("raw", "data/chain.jsonl", "Raw data")]
    nav = "".join(f"<a href='{h}'{' aria-current=page' if k == current else ''}>{t}</a>" for k, h, t in links)
    return (f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)}</title>{FONTS}<style>{CSS}</style></head><body><div class='wrap'>"
            f"<div class='top'><a class='brand' href='index.html'>{MARK}<span><b>{e(cfg['title'])}</b>"
            f"<small>Every call hashed before its outcome is known</small></span></a><nav>{nav}</nav></div>"
            f"{body}<footer>{DISCLAIMER}</footer></div></body></html>")


def _fmt_day(iso: str) -> str:
    d = datetime.fromisoformat(iso[:10])
    return f"{d:%b} {d.day}, {d.year}"


def equity_curve(trades: list[dict[str, Any]]) -> str:
    """Cumulative R across resolved trades in exit order, as an inline SVG."""
    done = sorted((t for t in trades if t["group"] == "resolved" and t["r"] is not None),
                  key=lambda t: (t["exited_on"] or "", t["called"]))
    if not done:
        return ""
    W, H, L, R_, T, B = 1000, 300, 46, 70, 16, 34
    cum, pts = 0.0, [0.0]
    for t in done:
        cum += t["r"]
        pts.append(round(cum, 3))
    lo, hi = min(pts + [0.0]), max(pts + [0.0])
    pad = max(0.5, (hi - lo) * 0.12)
    lo, hi = lo - pad, hi + pad
    n = len(pts) - 1
    x = lambda i: L + (W - L - R_) * (i / max(n, 1))
    y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
    path = f"M{x(0):.1f},{y(0):.1f}" + "".join(f" H{x(i):.1f} V{y(v):.1f}" for i, v in enumerate(pts) if i)
    area = path + f" V{y(0):.1f} Z"
    step = 1 if hi - lo <= 6 else 2 if hi - lo <= 14 else 5
    ticks = range(int(lo // step) * step, int(hi) + step, step)
    grid = "".join(f"<line class='axis' x1='{L}' x2='{W - R_}' y1='{y(v):.1f}' y2='{y(v):.1f}'/>"
                   f"<text x='{L - 8}' y='{y(v) + 4:.1f}' text-anchor='end'>{v:+d}R</text>" for v in ticks if lo <= v <= hi and v != 0)
    dots = []
    for i, t in enumerate(done, start=1):
        cls = "w" if t["r"] > 0 else "l" if t["r"] < 0 else "f"
        tip = f"{t['ticker']} {t['direction']} by {t['agent']}: {t['r']:+.2f}R, closed {t['exited_on']}"
        dots.append(f"<circle class='{cls}' cx='{x(i):.1f}' cy='{y(pts[i]):.1f}' r='5'><title>{e(tip)}</title></circle>")
    end = pts[-1]
    first, last = done[0]["exited_on"] or done[0]["called"], done[-1]["exited_on"] or done[-1]["called"]
    label = f"Cumulative R across {n} resolved calls, from {first} to {last}, ending at {end:+.2f}R"
    return (f"<figure class='curve'><svg viewBox='0 0 {W} {H}' role='img' aria-label='{e(label)}'>{grid}"
            f"<line class='zero' x1='{L}' x2='{W - R_}' y1='{y(0):.1f}' y2='{y(0):.1f}'/><text x='{L - 8}' y='{y(0) + 4:.1f}' text-anchor='end'>0R</text>"
            f"<clipPath id='up'><rect x='0' y='0' width='{W}' height='{y(0):.1f}'/></clipPath>"
            f"<clipPath id='dn'><rect x='0' y='{y(0):.1f}' width='{W}' height='{H}'/></clipPath>"
            f"<path class='area' clip-path='url(#up)' d='{area}'/><path class='area neg' clip-path='url(#dn)' d='{area}'/>"
            f"<path class='line' d='{path}'/>{''.join(dots)}"
            f"<text class='end' x='{x(n) + 12:.1f}' y='{y(end) + 5:.1f}'>{end:+.2f}R</text>"
            f"<text x='{L}' y='{H - 8}'>{e(_fmt_day(first))}</text><text x='{W - R_}' y='{H - 8}' text-anchor='end'>{e(_fmt_day(last))}</text>"
            f"</svg></figure>")


def _section(title: str, blurb: str, content: str, sid: str = "") -> str:
    ident = f" id='{sid}'" if sid else ""
    return (f"<section class='s'{ident}><header><h2>{title}</h2>{f'<p>{blurb}</p>' if blurb else ''}</header>"
            f"<div>{content}</div></section>")


def _signed(x: float | None) -> str:
    if x is None:
        return "–"
    cls = "pos" if x > 0 else "neg" if x < 0 else ""
    return f"<span class='{cls}'>{x:+.2f}R</span>"


def build_site(ledger: Ledger, outcomes: dict[str, Outcome], out_dir: str | Path,
               positions: dict[str, dict[str, Any]] | None = None, now: datetime | None = None) -> Path:
    cfg = ledger.config
    out = Path(out_dir)
    (out / "data").mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    as_of = now.date().isoformat()
    badges = bool(cfg.get("position_badges"))  # off: rely on the blanket disclosure in the footer
    if positions is None:
        positions = load_positions(ledger.root / cfg["positions_file"]) if badges else {}

    chain, reveals = ledger.chain(), ledger.reveals()
    revealed = [Prediction.from_dict(r["record"]) for r in reveals if not is_close(r["record"])]
    sc = scorecard(revealed, outcomes)
    verify = ledger.verify()
    trades = trade_rows(revealed, outcomes)

    for name in ("chain.jsonl", "reveals.jsonl"):
        src = ledger.public / name
        if src.exists():
            shutil.copy(src, out / "data" / name)
        else:
            (out / "data" / name).write_text("")
    (out / "data" / "scorecard.json").write_text(json.dumps(sc, indent=2))

    o, t = sc["overall"], sc["totals"]
    body: list[str] = []
    if cfg.get("notice"):
        body.append(f"<p class='notice'>{e(cfg['notice'])}</p>")

    # -- hero: the track record itself
    res = t["resolved"]
    if res:
        wins = round((o["win_rate"] or 0) * res)
        headline = f"{wins} of {res} resolved calls made money."
        lede = (f"Together they returned {o['total_r']:+.2f}R, or {o['avg_r']:+.2f}R per call, where 1R is the loss the agent "
                "planned to take if its stop was hit. Every call was hashed and published before the market could prove it right or wrong.")
    elif chain:
        headline = "The first calls are in. None has resolved yet."
        lede = ("Each call is graded against daily prices until it hits its stop, its target or its time limit. "
                "The record starts with the first sealed call; nothing is backfilled.")
    else:
        headline = "Starting soon"
        lede = "No predictions sealed yet. The track record begins with the first sealed call; nothing is backfilled."
    body.append(f"<div class='hero'><h1>{e(headline)}</h1><p class='lede'>{e(lede)}</p></div>")
    body.append(equity_curve(trades))
    stats = [("Win rate", _pct(o["win_rate"])), ("Avg R per call", _signed(o["avg_r"])), ("Resolved", str(res)),
             ("Open", str(t.get("OPEN", 0) + t.get("CLOSING", 0))), ("Waiting for entry", str(t.get("PENDING_ENTRY", 0))),
             ("Records sealed", str(len(chain)))]
    body.append("<dl class='strip'>" + "".join(f"<div><dt>{a}</dt><dd>{b}</dd></div>" for a, b in stats) + "</dl>")

    # -- how calls ended
    if res or o["cancelled"]:
        parts = [("Target hit", t.get("TARGET_HIT", 0), "var(--gain)"), ("Stopped", t.get("STOPPED", 0), "var(--loss)"),
                 ("Expired", t.get("EXPIRED", 0), "var(--flat)"), ("Closed early", o["early_closes"], "var(--amber)")]
        total = sum(n for _, n, _ in parts) or 1
        bar = "".join(f"<i style='width:{n / total * 100:.2f}%;background:{c}' title='{lbl}: {n}'></i>" for lbl, n, c in parts if n)
        legend = "".join(f"<li><span class='sw' style='background:{c}'></span><b>{n}</b>{lbl}</li>" for lbl, n, c in parts)
        early = ""
        if o["early_closes"]:
            early = (f" Of the {o['early_closes']} closed early, {o['early_wins']} finished up and {o['early_losses']} down"
                     + (f", averaging {o['early_avg_r']:+.2f}R." if o["early_avg_r"] is not None else "."))
        cancelled = f" {o['cancelled']} call(s) were withdrawn before entry and take no R." if o["cancelled"] else ""
        content = (f"<div class='stack' role='img' aria-label='Outcomes of resolved calls'>{bar}</div><ul class='legend'>{legend}</ul>"
                   f"<p class='note'>A win is any call that exits above its entry (below it for a short). A call the agent withdraws "
                   f"after a thesis change exits at the next day's open, never at a price it picks. Target-hit rate, leaving early "
                   f"closes out: {_pct(o['hit_rate'])}.{early}{cancelled}</p>")
        body.append(_section("How calls ended", "Every resolved call, by what ended it.", content))

    # -- agents
    if sc["agents"]:
        max_abs = max((abs(a["avg_r"]) for a in sc["agents"] if a["avg_r"] is not None), default=1) or 1
        rows = []
        for a in sc["agents"]:
            ar = a["avg_r"]
            w = 0 if ar is None else min(50, abs(ar) / max_abs * 50)
            bar = ("" if ar is None else
                   f"<i style='left:50%;width:{w:.1f}%;background:var(--gain)'></i>" if ar >= 0 else
                   f"<i style='right:50%;width:{w:.1f}%;background:var(--loss)'></i>")
            rows.append(f"<tr><td class='tk'>{e(a['agent'])}</td><td class='hide-s' style='width:34%'><div class='div'>{bar}</div></td>"
                        f"<td class='r'>{_signed(ar)}</td><td class='r'>{_pct(a['win_rate'])}</td><td class='r'>{a['resolved']}</td>"
                        f"<td class='r hide-s'>{a['early_closes'] or '–'}</td></tr>")
        body.append(_section("Which agent earns its keep",
                             "Average R per resolved call. Bars grow right for profit, left for loss.",
                             "<table><tr><th>Agent</th><th class='hide-s'></th><th class='r'>Avg R</th><th class='r'>Win rate</th>"
                             "<th class='r'>Resolved</th><th class='r hide-s'>Closed early</th></tr>" + "".join(rows) + "</table>"))

    if sc["insights"]:
        body.append(_section("What the record says", "Patterns the grader found, once each agent has enough resolved calls.",
                             "<ul class='ins'>" + "".join(f"<li>{e(i)}</li>" for i in sc["insights"]) + "</ul>"))

    # -- recent calls
    if sc["recent"]:
        rows = []
        for r in sc["recent"][:12]:
            cls, label = STATUS_PILL.get(r["status"], ("neutral", r["status"]))
            if r["status"] == "CLOSED_EARLY":
                cls = "good" if (r.get("r") or 0) > 0 else "bad"
            if r.get("r") is not None and r["status"] in ("TARGET_HIT", "STOPPED", "EXPIRED", "CLOSED_EARLY"):
                label = f"{label} {r['r']:+.2f}R"
            badge = f" {position_badge(r['ticker'], positions, as_of)}" if badges else ""
            rows.append(f"<tr><td><span class='tk'>{e(r['ticker'])}</span><span class='dir {e(r['direction'])}'>{e(r['direction'].capitalize())}</span>{badge}"
                        f"<div class='claim'>{e(r['claim'])}</div></td>"
                        f"<td class='hide-s'>{e(r['agent'])}<div class='sub'>{e(_fmt_day(r['issued_at']))}, {r['confidence']:.0%} confident</div></td>"
                        f"<td class='r hide-s'>{r['entry']:,.2f}<div class='sub'>stop {r['stop']:,.2f}, target {r['target']:,.2f}</div></td>"
                        f"<td class='r'><span class='pill {cls}'>{e(label)}</span></td></tr>")
        body.append(_section("Latest calls", "The most recent revealed calls. <a href='trades.html'>See every call with entry and exit prices</a>.",
                             "<table><tr><th>Call</th><th class='hide-s'>Agent</th><th class='r hide-s'>Entry</th><th class='r'>Status</th></tr>"
                             + "".join(rows) + "</table>"))

    if sc["calibration"]:
        rows = "".join(f"<tr><td>{e(c['bucket'])}</td><td class='r'>{_pct(c['stated'])}</td><td class='r'>{_pct(c['actual'])}</td>"
                       f"<td class='r'>{c['n']}</td><td class='r'><span class='pill {VERDICT_PILL[c['verdict']]}'>{e(c['verdict'].capitalize())}</span></td></tr>"
                       for c in sc["calibration"])
        body.append(_section("Does stated confidence mean anything?",
                             f"Each call states the odds its target comes before its stop. Brier score "
                             f"{o['brier'] if o['brier'] is not None else '–'} (lower is better; 0.25 is a coin flip).",
                             "<table><tr><th>Agent said</th><th class='r'>Avg stated</th><th class='r'>Actually hit</th><th class='r'>Calls</th>"
                             f"<th class='r'>Verdict</th></tr>{rows}</table>"))

    # -- proof
    head = chain[-1]["hash"] if chain else "(empty)"
    status = ("<span class='pill good'>Chain verified</span>" if verify.ok
              else f"<span class='pill bad'>Verification failed: {len(verify.errors)} issue(s)</span>")
    proof = (f"<div class='proof'>{status}<span>{len(chain)} sealed, {len(reveals)} revealed</span>"
             f"<span>Updated {e(_fmt_day(as_of))}</span></div><p class='note'>Chain head <code>{head}</code></p>"
             "<p class='note'>Each call is published first as a hash, chained to every call before it, so it can't be edited or "
             "backdated. When it is revealed, anyone can recompute the hash. <a href='verify.html'>Check the whole record in your browser</a>.</p>")
    revealed_commits = {r["commitment"] for r in reveals}
    pending = [link for link in chain if link["commitment"] not in revealed_commits]
    if pending:
        delay = int(cfg["reveal_delay_days"])
        rows = "".join(f"<tr><td>{link['seq']}</td><td>{e(link['sealed_at'][:16].replace('T', ' '))} UTC</td>"
                       f"<td><code>{link['commitment'][:24]}…</code></td></tr>" for link in reversed(pending[-15:]))
        proof += (f"<details><summary>Sealed, awaiting reveal ({len(pending)})</summary><p class='note'>Only the hashes of these calls "
                  f"are public until the {delay}-day reveal delay passes or the call resolves.</p>"
                  f"<table><tr><th>#</th><th>Sealed</th><th>Commitment</th></tr>{rows}</table></details>")
    body.append(_section("Proof of time", "Why these numbers can be trusted.", proof))

    (out / "index.html").write_text(_page(cfg["title"], "".join(body), cfg, "index"), "utf-8")
    (out / "data" / "trades.json").write_text(json.dumps(trades, indent=2))
    (out / "trades.html").write_text(_page(f"Trades · {cfg['title']}", trades_body(trades), cfg, "trades"), "utf-8")
    (out / "verify.html").write_text(_page(f"Verify · {cfg['title']}", VERIFY_BODY, cfg, "verify"), "utf-8")
    (out / ".nojekyll").write_text("")
    return out


VERIFY_BODY = """
<div class='hero'><h1>Verify the whole record</h1><p class='lede'>
<p>This page re-checks every link in the hash chain and every revealed prediction in your browser, using only the public files.
Nothing is trusted from the server.</p></div><p style='margin-top:24px'><button id='run'>Run verification</button></p><pre id='res' class='note'></pre>
<section class='s'><header><h2>Verify one call</h2></header><div>
<p>Paste a line from <a href='data/reveals.jsonl'>reveals.jsonl</a>. The page recomputes <code>sha256(salt + record_json)</code>
and shows when that commitment was sealed.</p><textarea id='one' placeholder='{"commitment": "...", "salt": "...", "record_json": "...", ...}'></textarea>
<p><button id='check'>Check</button></p><pre id='oneres' class='note'></pre></div></section>
<script>
const G='0'.repeat(64);
async function sha(t){const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(t));return [...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('')}
async function lines(u){const t=await (await fetch(u,{cache:'no-store'})).text();return t.split('\\n').filter(Boolean).map(JSON.parse)}
document.getElementById('run').onclick=async()=>{const out=document.getElementById('res');out.textContent='Checking…';
const chain=await lines('data/chain.jsonl'),rev=await lines('data/reveals.jsonl');let prev=G,errs=[];const by={};
for(const [i,l] of chain.entries()){if(l.seq!==i)errs.push('seq gap at '+i);if(l.prev!==prev)errs.push('seq '+i+': broken link');
if(await sha(`${l.seq}|${l.sealed_at}|${l.commitment}|${l.prev}`)!==l.hash)errs.push('seq '+i+': hash mismatch');prev=l.hash;by[l.commitment]=l}
for(const r of rev){if(await sha(r.salt+r.record_json)!==r.commitment)errs.push((r.record.id||'?')+': does not match commitment');else if(!by[r.commitment])errs.push((r.record.id||'?')+': not in chain')}
out.textContent=errs.length?('FAILED\\n'+errs.join('\\n')):`OK: ${chain.length} chain links and ${rev.length} reveals verified.\\nHead ${prev}`};
document.getElementById('check').onclick=async()=>{const o=document.getElementById('oneres');try{const r=JSON.parse(document.getElementById('one').value);
const c=await sha(r.salt+r.record_json);if(c!==r.commitment){o.textContent='Does NOT match the stated commitment.';return}
const chain=await lines('data/chain.jsonl');const l=chain.find(x=>x.commitment===c);const rec=JSON.parse(r.record_json);
o.textContent=l?`Match. ${rec.ticker} ${rec.direction} by ${rec.source_agent} was sealed at ${l.sealed_at} (chain seq ${l.seq}).`:'Hash matches, but this commitment is not in the chain.'}catch(err){o.textContent='Could not read that line: '+err.message}};
</script>
"""


# ---------------------------------------------------------------- trades page
STATUS_LABEL = {"TARGET_HIT": "Target hit", "STOPPED": "Stopped", "EXPIRED": "Expired", "OPEN": "Open",
                "PENDING_ENTRY": "Waiting for entry", "NOT_TRIGGERED": "Not triggered", "CLOSED_EARLY": "Closed early",
                "CANCELLED": "Cancelled before entry", "CLOSING": "Closing at next open"}
GROUP = {"TARGET_HIT": "resolved", "STOPPED": "resolved", "EXPIRED": "resolved", "CLOSED_EARLY": "resolved", "OPEN": "open",
         "CLOSING": "open", "PENDING_ENTRY": "waiting", "NOT_TRIGGERED": "waiting", "CANCELLED": "cancelled"}


def trade_rows(preds: list[Prediction], outcomes: dict[str, Outcome]) -> list[dict[str, Any]]:
    rows = []
    for p in preds:
        o = outcomes.get(p.id) or Outcome(id=p.id, status="PENDING_ENTRY" if p.entry is not None else "OPEN")
        risk = abs(p.entry_ref - p.stop)
        reward = abs(p.target - p.entry_ref)
        held = None
        if o.fill_date and o.exit_date:
            held = (datetime.fromisoformat(o.exit_date) - datetime.fromisoformat(o.fill_date)).days
        rows.append({
            "id": p.id, "called": p.issued_at[:10], "ticker": p.ticker, "agent": p.source_agent,
            "direction": p.direction, "entry_plan": p.entry, "price_at_call": p.price_at_issue,
            "stop": p.stop, "target": p.target, "plan_rr": round(reward / risk, 2) if risk else None,
            "horizon_days": p.horizon_days, "confidence": p.confidence, "claim": p.claim,
            "status": o.status, "status_label": STATUS_LABEL.get(o.status, o.status), "group": GROUP.get(o.status, "open"),
            "entered_on": o.fill_date, "entered_at": o.fill_price, "exited_on": o.exit_date, "exited_at": o.exit_price,
            "r": o.r, "days_held": held, "early": bool(o.closed_by), "close_reason": o.close_reason,
            "closed_on": o.closed_on,
        })
    rows.sort(key=lambda r: (r["exited_on"] or "", r["called"]), reverse=True)
    return rows


def trades_body(trades: list[dict[str, Any]]) -> str:
    counts = {g: sum(1 for t in trades if t["group"] == g) for g in ("resolved", "open", "waiting")}
    counts["early"] = sum(1 for t in trades if t["early"])
    data = json.dumps(trades).replace("</", "<\\/")
    tabs = [("resolved", "Resolved"), ("open", "Open"), ("waiting", "Waiting for entry"), ("early", "Closed early"), ("all", "All")]
    tab_html = "".join(f"<button role='tab' data-g='{g}' aria-selected='false'>{label} ({len(trades) if g == 'all' else counts[g]})</button>"
                       for g, label in tabs)
    return f"""
<div class='hero'><h1>Every revealed call</h1>
<p class='lede'>What each agent planned, and what the grader saw in daily prices. Grading starts the trading day after the call;
a day that touches both stop and target counts as stopped; a gap through a level fills at the open. A call the agent withdrew
after a thesis change exits at the next day's open, or is cancelled if it was never entered.</p></div>
<div class='seg' role='tablist'>{tab_html}</div>
<div class='tw'><table id='trades'>
<thead><tr><th>Called</th><th>Call</th><th>Agent</th><th class='r'>Planned entry</th><th class='r'>Stop</th><th class='r'>Target</th>
<th class='r'>Plan R:R</th><th class='r'>Entered</th><th class='r'>Exited</th><th class='r'>Result</th></tr></thead><tbody></tbody></table></div>
<p class='empty' id='empty' hidden>No calls in this group yet.</p>
<p class='note'>R is the result divided by the planned risk (entry to stop). Plan R:R is the target distance divided by the stop distance.
Download the rows as <a href='data/trades.json'>trades.json</a>.</p>
<style>
.wrap{{max-width:1280px}}
.seg{{display:inline-flex;flex-wrap:wrap;gap:2px;padding:3px;margin:26px 0 14px;background:var(--soft);border-radius:10px}}
.seg button{{font:500 13.5px var(--sans);padding:6px 14px;border-radius:8px;border:0;background:transparent;color:var(--muted)}}
.seg button[aria-selected=true]{{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(20,33,61,.12)}}
.tw{{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:6px 18px}}
#trades td{{white-space:nowrap}}#trades td:last-child{{white-space:normal;min-width:180px}}
#trades tbody tr:hover td{{background:var(--paper)}}
.res{{font:600 17px var(--cond)}}.why{{display:block;color:var(--muted);font-size:12.5px;margin-top:3px;white-space:normal}}
</style>
<script>
const T={data};
const f=(x,d=2)=>x===null||x===undefined?'–':Number(x).toLocaleString(undefined,{{minimumFractionDigits:d,maximumFractionDigits:d}});
const esc=x=>String(x).replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
const day=s=>{{if(!s)return '';const d=new Date(s+'T12:00:00');return d.toLocaleDateString(undefined,{{month:'short',day:'numeric'}})}};
const tone={{TARGET_HIT:'good',STOPPED:'bad',EXPIRED:'neutral',OPEN:'info',PENDING_ENTRY:'info',NOT_TRIGGERED:'neutral',CANCELLED:'neutral',CLOSING:'warn'}};
function result(t){{const c=t.status==='CLOSED_EARLY'?(t.r>0?'good':'bad'):(tone[t.status]||'neutral');
 const r=t.r===null?'':`<span class='res ${{t.r>0?'pos':t.r<0?'neg':''}}'>${{t.r>0?'+':''}}${{f(t.r)}}R</span> `;
 const why=t.early?`<span class='why'>Withdrawn ${{day(t.closed_on)}}${{t.close_reason?': '+esc(t.close_reason):''}}</span>`:'';
 return `${{r}}<span class='pill ${{c}}'>${{t.status_label}}</span>${{why}}`}}
function render(g){{const rows=T.filter(t=>g==='all'||(g==='early'?t.early:t.group===g));const tb=document.querySelector('#trades tbody');
 tb.innerHTML=rows.map(t=>`<tr><td>${{day(t.called)}}</td><td><span class='tk'>${{t.ticker}}</span><span class='dir ${{t.direction}}'>${{t.direction==='long'?'Long':'Short'}}</span></td>
 <td>${{t.agent}}</td><td class='r'>${{t.entry_plan===null?'Market <span class=sub>'+f(t.price_at_call)+'</span>':f(t.entry_plan)}}</td>
 <td class='r'>${{f(t.stop)}}</td><td class='r'>${{f(t.target)}}</td><td class='r'>${{t.plan_rr===null?'–':f(t.plan_rr)}}</td>
 <td class='r'>${{t.entered_on?f(t.entered_at)+'<div class=sub>'+day(t.entered_on)+'</div>':'–'}}</td>
 <td class='r'>${{t.exited_on?f(t.exited_at)+'<div class=sub>'+day(t.exited_on)+(t.days_held!==null?', '+t.days_held+'d':'')+'</div>':'–'}}</td>
 <td class='r'>${{result(t)}}</td></tr>`).join('');
 document.getElementById('empty').hidden=rows.length>0;document.querySelector('.tw').hidden=rows.length===0;
 document.querySelectorAll('.seg button').forEach(b=>b.setAttribute('aria-selected',b.dataset.g===g))}}
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{{render(b.dataset.g);history.replaceState(null,'','?show='+b.dataset.g)}});
render(new URLSearchParams(location.search).get('show')||'resolved');
</script>"""
