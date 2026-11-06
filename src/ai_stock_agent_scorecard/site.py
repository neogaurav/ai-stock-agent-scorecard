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
:root{--bg:#fbfaf7;--card:#fff;--soft:#f3f1ec;--text:#1f1e1c;--muted:#6b6963;--line:#e4e1da;
--good:#0f6e56;--goodbg:#e1f5ee;--bad:#a32d2d;--badbg:#fcebeb;--info:#185fa5;--infobg:#e6f1fb;--warn:#854f0b;--warnbg:#faeeda}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--soft:#262624;--text:#ecebe6;--muted:#a3a19a;
--line:#34332f;--good:#5dcaa5;--goodbg:#0c3a2f;--bad:#f09595;--badbg:#4a1b1b;--info:#85b7eb;--infobg:#0c2c4f;--warn:#ef9f27;--warnbg:#412402}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:920px;margin:0 auto;padding:28px 20px 60px}a{color:var(--info)}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:20px}
h1{font-size:22px;font-weight:600;margin:0}h2{font-size:16px;font-weight:600;margin:0 0 12px}
.sub{color:var(--muted);font-size:13px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}
.m{background:var(--soft);border-radius:10px;padding:14px 16px}.m p{margin:0}.ml{font-size:13px;color:var(--muted)}.mv{font-size:24px;font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 20px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:13.5px}th,td{text-align:left;padding:7px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500}.pill{font-size:12px;padding:2px 8px;border-radius:6px;white-space:nowrap;display:inline-block}
.good{background:var(--goodbg);color:var(--good)}.bad{background:var(--badbg);color:var(--bad)}.info{background:var(--infobg);color:var(--info)}
.warn{background:var(--warnbg);color:var(--warn)}.neutral{background:var(--soft);color:var(--muted)}
.bar{height:8px;border-radius:4px;background:var(--soft);position:relative;overflow:hidden;min-width:80px}.bar i{position:absolute;top:0;bottom:0;border-radius:4px}
ul.ins{margin:0;padding-left:18px}footer{font-size:12.5px;color:var(--muted);border-top:1px solid var(--line);margin-top:28px;padding-top:12px}
code{font-size:12px;background:var(--soft);padding:1px 5px;border-radius:4px}.num{font-variant-numeric:tabular-nums}
nav a{margin-left:14px;font-size:14px}textarea{width:100%;min-height:120px;font:12px ui-monospace,monospace;background:var(--soft);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:10px}
button{font:inherit;padding:6px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer}
"""

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


def _page(title: str, body: str, cfg: dict[str, Any]) -> str:
    return (f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)}</title><style>{CSS}</style></head><body><main>"
            f"<header><div><h1>{e(cfg['title'])}</h1><div class='sub'>Lookahead-free track record of AI research agents</div></div>"
            f"<nav><a href='index.html'>Scorecard</a><a href='trades.html'>Trades</a><a href='verify.html'>Verify</a><a href='data/chain.jsonl'>Raw data</a></nav></header>"
            f"{body}<footer>{DISCLAIMER}</footer></main></body></html>")


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

    for name in ("chain.jsonl", "reveals.jsonl"):
        src = ledger.public / name
        if src.exists():
            shutil.copy(src, out / "data" / name)
        else:
            (out / "data" / name).write_text("")
    (out / "data" / "scorecard.json").write_text(json.dumps(sc, indent=2))

    o = sc["overall"]
    head = chain[-1]["hash"] if chain else "(empty)"
    status = ("<span class='pill good'>Chain verified</span>" if verify.ok
              else f"<span class='pill bad'>Verification failed: {len(verify.errors)} issue(s)</span>")
    notice = cfg.get("notice")
    body = [f"<div class='card' style='border-color:var(--warn)'><span class='pill warn'>Note</span> {e(notice)}</div>"] if notice else []
    body += [f"<p class='sub'>{status} &nbsp;{len(chain)} sealed · {len(reveals)} revealed · head <code>{head[:16]}</code> · updated {e(as_of)}</p>"]
    body.append("<div class='grid'>"
                f"<div class='m'><p class='ml'>Records sealed</p><p class='mv num'>{len(chain)}</p></div>"
                f"<div class='m'><p class='ml'>Resolved (revealed)</p><p class='mv num'>{sc['totals']['resolved']}</p></div>"
                f"<div class='m'><p class='ml'>Win rate</p><p class='mv num'>{_pct(o['win_rate'])}</p></div>"
                f"<div class='m'><p class='ml'>Avg R per call</p><p class='mv num'>{_r(o['avg_r'])}</p></div>"
                "</div>")

    t = sc["totals"]
    if t["resolved"] or o["cancelled"]:
        def cell(label, n, extra=""):
            return f"<div class='m'><p class='ml'>{label}</p><p class='mv num'>{n}</p>{extra}</div>"
        early = (f"<p class='sub'>{o['early_wins']} up · {o['early_losses']} down"
                 + (f" · {_r(o['early_avg_r'])} avg" if o["early_avg_r"] is not None else "") + "</p>")
        body.append("<div class='card'><h2>How calls ended</h2><div class='grid'>"
                    + cell("Target hit", t.get("TARGET_HIT", 0)) + cell("Stopped", t.get("STOPPED", 0))
                    + cell("Expired", t.get("EXPIRED", 0)) + cell("Closed early", o["early_closes"], early)
                    + "</div><p class='sub'>Win rate counts every call that was entered and finished: a target hit, or any other exit "
                    "above the entry (below it for shorts). Closed early means the agent withdrew the call after a thesis change; "
                    "it exits at the next day's open, never at a price the agent picks, and counts as a win or loss like any other trade. "
                    f"Target-hit rate, leaving early closes out: {_pct(o['hit_rate'])}."
                    + (f" {o['cancelled']} call(s) were withdrawn before entry and take no R." if o["cancelled"] else "")
                    + "</p></div>")

    if sc["agents"]:
        max_abs = max((abs(a["avg_r"]) for a in sc["agents"] if a["avg_r"] is not None), default=1) or 1
        rows = []
        for a in sc["agents"]:
            ar = a["avg_r"]
            width = 0 if ar is None else min(100, abs(ar) / max_abs * 100)
            color = "var(--good)" if (ar or 0) >= 0 else "var(--bad)"
            rows.append(f"<tr><td>{e(a['agent'])}</td><td style='width:40%'><div class='bar'><i style='width:{width:.0f}%;background:{color}'></i></div></td>"
                        f"<td class='num'>{_r(ar)}</td><td class='num'>{_pct(a['win_rate'])}</td><td class='num'>{a['resolved']}</td>"
                        f"<td class='num'>{a['early_closes'] or '–'}</td></tr>")
        body.append("<div class='card'><h2>Which agent earns its keep</h2><table><tr><th>Agent</th><th></th><th>Avg R</th><th>Win rate</th><th>Resolved</th><th>Closed early</th></tr>"
                    + "".join(rows) + "</table></div>")

    if sc["insights"]:
        body.append("<div class='card'><h2>What the record says</h2><ul class='ins'>"
                    + "".join(f"<li>{e(i)}</li>" for i in sc["insights"]) + "</ul></div>")

    if sc["calibration"]:
        rows = "".join(f"<tr><td>{e(c['bucket'])} confident</td><td class='num'>{_pct(c['actual'])}</td><td class='num'>{c['n']}</td>"
                       f"<td><span class='pill {VERDICT_PILL[c['verdict']]}'>{e(c['verdict'].capitalize())}</span></td></tr>"
                       for c in sc["calibration"])
        body.append("<div class='card'><h2>Calibration: does stated confidence mean anything?</h2>"
                    f"<table><tr><th>Agent said</th><th>Actually hit</th><th>n</th><th>Verdict</th></tr>{rows}</table>"
                    f"<p class='sub'>Brier score {o['brier'] if o['brier'] is not None else '–'} (lower is better; 0.25 = coin flip at 50%).</p></div>")

    if sc["recent"]:
        rows = []
        for r in sc["recent"]:
            cls, label = STATUS_PILL.get(r["status"], ("neutral", r["status"]))
            if r.get("r") is not None and r["status"] in ("TARGET_HIT", "STOPPED", "EXPIRED", "CLOSED_EARLY"):
                label = f"{label} {r['r']:+.1f}R"
            badge = f"<br>{position_badge(r['ticker'], positions, as_of)}" if badges else ""
            rows.append(f"<tr><td><strong>{e(r['ticker'])}</strong> <span class='sub'>{e(r['direction'])}</span>{badge}</td>"
                        f"<td>{e(r['agent'])}<br><span class='sub'>{e(r['issued_at'][:10])} · {r['confidence']:.0%}</span></td>"
                        f"<td class='num'>{r['entry']:.2f} / {r['stop']:.2f} / {r['target']:.2f}<br><span class='sub'>{e(r['claim'])}</span></td>"
                        f"<td><span class='pill {cls}'>{e(label)}</span></td></tr>")
        body.append("<div class='card'><h2>Revealed predictions</h2><table><tr><th>Ticker</th><th>Agent</th><th>Entry / stop / target</th><th>Outcome</th></tr>"
                    + "".join(rows) + "</table><p class='sub'><a href='trades.html'>See every call with entry and exit details</a>. Unrevealed calls appear only as hashes until their reveal delay passes.</p></div>")

    revealed_commits = {r["commitment"] for r in reveals}
    pending = [link for link in chain if link["commitment"] not in revealed_commits]
    if pending:
        delay = int(cfg["reveal_delay_days"])
        rows = "".join(f"<tr><td class='num'>{link['seq']}</td><td>{e(link['sealed_at'][:16].replace('T', ' '))} UTC</td>"
                       f"<td><code>{link['commitment'][:24]}…</code></td></tr>" for link in reversed(pending[-15:]))
        body.append(f"<div class='card'><h2>Sealed, awaiting reveal ({len(pending)})</h2>"
                    f"<p class='sub'>These calls are locked in. Only their hashes are public until the {delay}-day reveal delay passes "
                    "or the call resolves; then the full call appears above and anyone can check it against this hash.</p>"
                    f"<table><tr><th>#</th><th>Sealed</th><th>Commitment</th></tr>{rows}</table></div>")
    if not chain:
        body.append("<div class='card'><h2>Starting soon</h2><p>No predictions sealed yet. The track record begins with the first "
                    "sealed call; nothing is backfilled.</p></div>")

    (out / "index.html").write_text(_page(cfg["title"], "".join(body), cfg), "utf-8")

    trades = trade_rows(revealed, outcomes)
    (out / "data" / "trades.json").write_text(json.dumps(trades, indent=2))
    (out / "trades.html").write_text(_page(f"Trades · {cfg['title']}", trades_body(trades), cfg), "utf-8")
    (out / "verify.html").write_text(_page(f"Verify · {cfg['title']}", VERIFY_BODY, cfg), "utf-8")
    (out / ".nojekyll").write_text("")
    return out


VERIFY_BODY = """
<div class='card'><h2>Verify the whole record</h2>
<p>This page re-checks every link in the hash chain and every revealed prediction in your browser, using only the public files.
Nothing is trusted from the server.</p><button id='run'>Run verification</button><pre id='res' class='sub'></pre></div>
<div class='card'><h2>Verify one prediction</h2>
<p>Paste a line from <a href='data/reveals.jsonl'>reveals.jsonl</a>. The page recomputes <code>sha256(salt + record_json)</code>
and shows when that commitment was sealed.</p><textarea id='one' placeholder='{"commitment": "...", "salt": "...", "record_json": "...", ...}'></textarea>
<p><button id='check'>Check</button></p><pre id='oneres' class='sub'></pre></div>
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
    return f"""
<div class='card'>
<h2>Every revealed call</h2>
<p class='sub'>Planned levels are what the agent wrote when it made the call. Entered and exited are what the grader saw in
daily prices: grading starts the trading day after the call, a day that touches both stop and target counts as stopped,
and a gap through a level fills at the open. A call the agent withdrew after a thesis change exits at the next day's open
(or is cancelled if it was never entered). R = result ÷ planned risk (entry to stop). Plan R:R = target distance ÷ stop distance.</p>
<div class='tabs' role='tablist'>
 <button role='tab' data-g='resolved' aria-selected='true'>Resolved ({counts['resolved']})</button>
 <button role='tab' data-g='open' aria-selected='false'>Open ({counts['open']})</button>
 <button role='tab' data-g='waiting' aria-selected='false'>Waiting for entry ({counts['waiting']})</button>
 <button role='tab' data-g='early' aria-selected='false'>Closed early ({counts['early']})</button>
 <button role='tab' data-g='all' aria-selected='false'>All ({len(trades)})</button>
</div>
<div class='tw'><table id='trades'>
<thead><tr><th>Called</th><th>Ticker</th><th>Agent</th><th>Planned entry</th><th>Stop</th><th>Target</th><th>Plan R:R</th>
<th>Entered</th><th>Exited</th><th>Outcome</th></tr></thead><tbody></tbody></table></div>
<p class='sub' id='empty' hidden>No calls in this group yet.</p>
<p class='sub'>Download the raw rows as <a href='data/trades.json'>trades.json</a>.</p>
</div>
<style>
main{{max-width:1240px}}
.tabs{{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0 10px}}
.tabs button{{font:inherit;font-size:13.5px;padding:5px 12px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--muted);cursor:pointer}}
.tabs button[aria-selected=true]{{background:var(--text);color:var(--bg);border-color:var(--text)}}
.tw{{overflow-x:auto}} #trades td{{white-space:nowrap}} #trades td:last-child{{white-space:normal;min-width:170px}}
.dir{{font-size:12px;color:var(--muted)}}
</style>
<script>
const T={data};
const f=(x,d=2)=>x===null||x===undefined?'–':Number(x).toLocaleString(undefined,{{minimumFractionDigits:d,maximumFractionDigits:d}});
const pill=t=>{{const c={{TARGET_HIT:'good',STOPPED:'bad',EXPIRED:'neutral',OPEN:'info',PENDING_ENTRY:'info',NOT_TRIGGERED:'neutral',CANCELLED:'neutral',CLOSING:'warn',
 CLOSED_EARLY:t.r>0?'good':'bad'}}[t.status]||'neutral';
 const r=t.r===null?'':' '+(t.r>0?'+':'')+f(t.r)+'R';return `<span class='pill ${{c}}'>${{t.status_label}}${{r}}</span>`}};
const esc=x=>String(x).replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
function render(g){{const rows=T.filter(t=>g==='all'||(g==='early'?t.early:t.group===g));const tb=document.querySelector('#trades tbody');
 tb.innerHTML=rows.map(t=>`<tr><td>${{t.called}}</td><td><strong>${{t.ticker}}</strong> <span class='dir'>${{t.direction}}</span></td><td>${{t.agent}}</td>
 <td class='num'>${{t.entry_plan===null?'Market ('+f(t.price_at_call)+')':f(t.entry_plan)}}</td><td class='num'>${{f(t.stop)}}</td><td class='num'>${{f(t.target)}}</td>
 <td class='num'>${{t.plan_rr===null?'–':f(t.plan_rr)}}</td>
 <td class='num'>${{t.entered_on?f(t.entered_at)+'<br><span class=sub>'+t.entered_on+'</span>':'–'}}</td>
 <td class='num'>${{t.exited_on?f(t.exited_at)+'<br><span class=sub>'+t.exited_on+(t.days_held!==null?' · '+t.days_held+'d':'')+'</span>':'–'}}</td>
 <td>${{pill(t)}}${{t.early?'<br><span class=sub>Withdrawn '+t.closed_on+(t.close_reason?': '+esc(t.close_reason):'')+'</span>':''}}</td></tr>`).join('');
 document.getElementById('empty').hidden=rows.length>0;
 document.querySelectorAll('.tabs button').forEach(b=>b.setAttribute('aria-selected',b.dataset.g===g))}}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>render(b.dataset.g));
render(new URLSearchParams(location.search).get('show')||'resolved');
</script>"""
