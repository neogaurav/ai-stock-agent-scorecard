"""Build a demo ledger + site from synthetic prices (no network, fake tickers).

    python examples/demo.py && open demo-site/index.html
"""
from __future__ import annotations

import random
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from thesis_ledger import Ledger, Prediction  # noqa: E402
from thesis_ledger.prices import Bar, StaticProvider  # noqa: E402
from thesis_ledger.score import grade_all  # noqa: E402
from thesis_ledger.site import build_site  # noqa: E402

random.seed(7)
ROOT, SITE = Path("demo-ledger"), Path("demo-site")
TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]
# (agent, edge, confidence bias) -- edge nudges the path toward the call
AGENTS = [("options-flow", 0.3, 0.0), ("technicals", 0.15, 0.05), ("pm", 0.12, 0.0),
          ("fundamentals", -0.35, 0.15), ("human", 0.02, -0.05)]
START, DAYS = date(2026, 4, 1), 150


def trading_days(start, n):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


days = trading_days(START, DAYS)
paths = {}
for t in TICKERS:
    px, rows = random.uniform(30, 300), []
    for d in days:
        o = px * (1 + random.gauss(0, 0.004))
        c = o * (1 + random.gauss(0, 0.013))
        rows.append([d, o, max(o, c) * (1 + abs(random.gauss(0, 0.008))), min(o, c) * (1 - abs(random.gauss(0, 0.008))), c])
        px = c
    paths[t] = rows

preds = []
for i in range(170):
    t = random.choice(TICKERS)
    agent, edge, bias = random.choice(AGENTS)
    k = random.randrange(5, DAYS - 70)
    issue_day, px = days[k], paths[t][k][4]
    direction = "long" if random.random() < 0.7 else "short"
    risk = px * random.uniform(0.04, 0.08)
    rr = random.choice([1.5, 2, 2.5, 3])
    stop = px - risk if direction == "long" else px + risk
    target = px + rr * risk if direction == "long" else px - rr * risk
    horizon = random.choice([10, 20, 20, 40, 60])
    if agent == "fundamentals" and horizon >= 40:
        edge = 0.3  # fundamentals only work on long horizons
    conf = min(0.92, max(0.4, random.gauss(0.6 + bias, 0.1)))
    sign = 1 if direction == "long" else -1
    drift = 1 + sign * edge * rr * risk / px / horizon  # plant the agent's edge in the future path
    for j in range(k + 1, DAYS):
        factor = drift ** min(j - k, horizon)
        for col in (1, 2, 3, 4):
            paths[t][j][col] *= factor
    preds.append(Prediction(ticker=t, direction=direction, entry=None, stop=round(stop, 2), target=round(target, 2),
                            horizon_days=horizon, confidence=round(conf, 2), price_at_issue=round(px, 2),
                            source_agent=agent, issued_at=f"{issue_day}T15:00:00+00:00",
                            claim=random.choice(["Breakout above range high", "Pullback to 20-day MA holds",
                                                 "Put wall support, IV rich", "Earnings drift continuation",
                                                 "Mean reversion from oversold", "Guidance reset priced in"])))

shutil.rmtree(ROOT, ignore_errors=True)
shutil.rmtree(SITE, ignore_errors=True)
led = Ledger(ROOT)
led.init(title="Thesis Ledger (demo data)", reveal_delay_days=5,
         notice="Demo data: synthetic prices and made-up tickers (AAA-HHH) that show how the scorecard works. "
                "The live track record will replace this once real predictions have history.")
for p in sorted(preds, key=lambda p: p.issued_at):
    led.seal([p], sealed_at=p.issued_at.replace("15:00:00", "15:01:00"))  # demo only: backfilled seal times

provider = StaticProvider({t: [Bar(r[0], *r[1:]) for r in rows] for t, rows in paths.items()})
asof = days[-1]
outcomes = grade_all(led.predictions(), provider, today=asof)
led.reveal_due(now=datetime.combine(asof, datetime.min.time(), tzinfo=timezone.utc),
               resolved_ids={k for k, v in outcomes.items() if v.resolved})
build_site(led, outcomes, SITE, positions={"AAA": {"side": "long", "qty": 100}},
           now=datetime.combine(asof, datetime.min.time(), tzinfo=timezone.utc))
print(f"{len(led.chain())} sealed, {len(led.reveals())} revealed, verify ok={led.verify().ok}")
print(f"open {SITE / 'index.html'}")
