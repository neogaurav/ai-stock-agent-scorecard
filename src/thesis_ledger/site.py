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
from .schema import Prediction
from .score import Outcome, scorecard

DISCLAIMER = (
    "<p><strong>For educational and research purposes only.</strong> All predictions on this site are generated "
    "automatically by AI agents and are published to evaluate how accurate those agents are. They are not investment "
    "advice, recommendations, or solicitations to buy or sell any security, and they do not take into account your "
    "financial situation or objectives.</p>"
    "<p><strong>Trading involves substantial risk of loss.</strong> Past accuracy, including the scores shown here, does "
    "not guarantee future results. Predictions may be wrong, outdated, or based on incomplete data. Do your own research "
    "and consult a licensed financial professional before making investment decisions.</p>"
    "<p><strong>Author positions.</strong> The author may hold, buy, or sell positions in securities discussed here. "
    "Current author positions are disclosed next to each ticker. Predictions are revealed after a delay and are not "
    "trading signals.</p>"
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
            f"<nav><a href='index.html'>Scorecard</a><a href='verify.html'>Verify</a><a href='data/chain.jsonl'>Raw data</a></nav></header>"
            f"{body}<footer>{DISCLAIMER}</footer></main></body></html>")


def build_site(ledger: Ledger, outcomes: dict[str, Outcome], out_dir: str | Path,
               positions: dict[str, dict[str, Any]] | None = None, now: datetime | None = None) -> Path:
    cfg = ledger.config
    out = Path(out_dir)
    (out / "data").mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    as_of = now.date().isoformat()
    if positions is None:
        positions = load_positions(ledger.root / cfg["positions_file"])

    chain, reveals = ledger.chain(), ledger.reveals()
    revealed = [Prediction.from_dict(r["record"]) for r in reveals]
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
                f"<div class='m'><p class='ml'>Predictions sealed</p><p class='mv num'>{len(chain)}</p></div>"
                f"<div class='m'><p class='ml'>Resolved (revealed)</p><p class='mv num'>{sc['totals']['resolved']}</p></div>"
                f"<div class='m'><p class='ml'>Hit rate</p><p class='mv num'>{_pct(o['hit_rate'])}</p></div>"
                f"<div class='m'><p class='ml'>Avg R per call</p><p class='mv num'>{_r(o['avg_r'])}</p></div>"
                "</div>")

    if sc["agents"]:
        max_abs = max((abs(a["avg_r"]) for a in sc["agents"] if a["avg_r"] is not None), default=1) or 1
        rows = []
        for a in sc["agents"]:
            ar = a["avg_r"]
            width = 0 if ar is None else min(100, abs(ar) / max_abs * 100)
            color = "var(--good)" if (ar or 0) >= 0 else "var(--bad)"
            rows.append(f"<tr><td>{e(a['agent'])}</td><td style='width:40%'><div class='bar'><i style='width:{width:.0f}%;background:{color}'></i></div></td>"
                        f"<td class='num'>{_r(ar)}</td><td class='num'>{_pct(a['hit_rate'])}</td><td class='num'>{a['resolved']}</td></tr>")
        body.append("<div class='card'><h2>Which agent earns its keep</h2><table><tr><th>Agent</th><th></th><th>Avg R</th><th>Hit rate</th><th>Resolved</th></tr>"
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
            if r.get("r") is not None and r["status"] in ("TARGET_HIT", "STOPPED", "EXPIRED"):
                label = f"{label} {r['r']:+.1f}R"
            rows.append(f"<tr><td><strong>{e(r['ticker'])}</strong> <span class='sub'>{e(r['direction'])}</span><br>{position_badge(r['ticker'], positions, as_of)}</td>"
                        f"<td>{e(r['agent'])}<br><span class='sub'>{e(r['issued_at'][:10])} · {r['confidence']:.0%}</span></td>"
                        f"<td class='num'>{r['entry']:.2f} / {r['stop']:.2f} / {r['target']:.2f}<br><span class='sub'>{e(r['claim'])}</span></td>"
                        f"<td><span class='pill {cls}'>{e(label)}</span></td></tr>")
        body.append("<div class='card'><h2>Revealed predictions</h2><table><tr><th>Ticker</th><th>Agent</th><th>Entry / stop / target</th><th>Outcome</th></tr>"
                    + "".join(rows) + "</table><p class='sub'>Unrevealed calls appear only as hashes in the chain until their reveal delay passes.</p></div>")

    (out / "index.html").write_text(_page(cfg["title"], "".join(body), cfg), "utf-8")
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
