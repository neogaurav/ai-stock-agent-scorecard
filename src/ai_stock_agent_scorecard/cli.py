"""agent-scorecard command line."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .extract import extract_blocks, ingest_folder
from .ledger import Ledger
from .prices import CSVProvider, YFinanceProvider
from .schema import Prediction
from .score import Outcome, grade_all, scorecard
from .site import build_site


def _ledger(args) -> Ledger:
    return Ledger(args.root)


def _provider(args):
    return CSVProvider(args.prices_csv) if getattr(args, "prices_csv", None) else YFinanceProvider()


def outcomes_path(ledger: Ledger) -> Path:
    return ledger.private / "outcomes.json"


def load_outcomes(ledger: Ledger) -> dict[str, Outcome]:
    p = outcomes_path(ledger)
    if not p.exists():
        return {}
    return {k: Outcome(**v) for k, v in json.loads(p.read_text()).items()}


def cmd_init(args):
    _ledger(args).init(title=args.title, reveal_delay_days=args.reveal_delay_days)
    print(f"Initialised ledger at {args.root}. Keep private/ out of any public repo.")


def cmd_seal(args):
    led = _ledger(args)
    raw = Path(args.file).read_text() if args.file and args.file != "-" else sys.stdin.read()
    raw = raw.strip()
    if raw.startswith("{") or raw.startswith("["):
        data = json.loads(raw)
        preds = [Prediction.from_dict(d) for d in (data if isinstance(data, list) else [data])]
        errors = []
    else:
        preds, errors = extract_blocks(raw)
    links = led.seal(preds, git_commit=args.git)
    for err in errors:
        print(f"skipped: {err}", file=sys.stderr)
    print(f"Sealed {len(links)} prediction(s)" + (f" · head {links[-1]['hash'][:12]}" if links else ""))
    rep = led.verify()
    print("Chain verified" if rep.ok else f"Chain problems: {rep.errors}")


def cmd_ingest(args):
    patterns = tuple(args.pattern) if args.pattern else ("*.md", "*.txt")
    summary = ingest_folder(args.folder, _ledger(args), use_llm=args.llm, patterns=patterns, git_commit=args.git)
    print(f"Scanned {summary['files']} new/changed file(s), sealed {summary['sealed']} prediction(s)"
          + (f", refused {summary['too_old']} too old to seal fairly" if summary["too_old"] else ""))
    for err in summary["errors"]:
        print(f"skipped: {err}", file=sys.stderr)


def cmd_score(args):
    led = _ledger(args)
    preds = led.predictions()
    outcomes = grade_all(preds, _provider(args), today=date.fromisoformat(args.today) if args.today else None)
    outcomes_path(led).write_text(json.dumps({k: v.to_dict() for k, v in outcomes.items()}, indent=2))
    sc = scorecard(preds, outcomes)
    if args.json:
        print(json.dumps(sc, indent=2))
        return
    o = sc["overall"]
    print(f"{sc['totals']['predictions']} predictions · {sc['totals']['resolved']} resolved · "
          f"hit rate {o['hit_rate'] if o['hit_rate'] is not None else '–'} · avg R {o['avg_r'] if o['avg_r'] is not None else '–'}")
    for a in sc["agents"]:
        print(f"  {a['agent']:<14} n={a['resolved']:<4} hit={a['hit_rate']} avgR={a['avg_r']}")
    for line in sc["insights"]:
        print(f"  • {line}")


def cmd_reveal(args):
    led = _ledger(args)
    resolved = {k for k, v in load_outcomes(led).items() if v.resolved}
    out = led.reveal_due(resolved_ids=resolved, git_commit=args.git)
    print(f"Revealed {len(out)} prediction(s)")


def cmd_verify(args):
    rep = _ledger(args).verify()
    print(f"{rep.chain_length} links, {rep.reveals_checked} reveals checked")
    if rep.ok:
        print("OK")
    else:
        print("\n".join(rep.errors))
        sys.exit(1)


def cmd_site(args):
    led = _ledger(args)
    out = build_site(led, load_outcomes(led), args.out)
    print(f"Site written to {out}/index.html")


def cmd_nightly(args):
    """score → reveal → site, the one command the scheduler runs."""
    cmd_score(argparse.Namespace(**{**vars(args), "json": False, "today": None}))
    cmd_reveal(args)
    cmd_site(args)


def cmd_mcp(args):
    from .mcp_server import run

    os.environ.setdefault("SCORECARD_ROOT", args.root)
    run()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agent-scorecard", description="Lookahead-free track record for AI research agents")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--root", default=os.environ.get("SCORECARD_ROOT", "ledger"), help="ledger directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create a ledger")
    p.add_argument("--title", default="AI Stock Agent Scorecard")
    p.add_argument("--reveal-delay-days", type=int, default=5)
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("seal", help="seal predictions from JSON or a report with ```prediction blocks")
    p.add_argument("file", nargs="?", default="-")
    p.add_argument("--git", action="store_true", help="git commit public files after sealing")
    p.set_defaults(fn=cmd_seal)

    p = sub.add_parser("ingest", help="seal predictions from every new report in a folder")
    p.add_argument("folder")
    p.add_argument("--llm", action="store_true", help="fall back to LLM extraction for free-form reports")
    p.add_argument("--pattern", action="append", help="file glob to scan (repeatable), e.g. decision.md")
    p.add_argument("--git", action="store_true")
    p.set_defaults(fn=cmd_ingest)

    for name, fn, help_ in (("score", cmd_score, "grade predictions against daily prices"),
                            ("nightly", cmd_nightly, "score, reveal and rebuild the site")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--prices-csv", help="directory of <TICKER>.csv files instead of yfinance")
        if name == "score":
            p.add_argument("--json", action="store_true")
            p.add_argument("--today", help="grade as of YYYY-MM-DD")
        else:
            p.add_argument("--out", default="site")
            p.add_argument("--git", action="store_true")
        p.set_defaults(fn=fn)

    p = sub.add_parser("reveal", help="reveal predictions whose delay has passed or that resolved")
    p.add_argument("--git", action="store_true")
    p.set_defaults(fn=cmd_reveal)

    sub.add_parser("verify", help="verify chain and reveals").set_defaults(fn=cmd_verify)

    p = sub.add_parser("site", help="build the static site")
    p.add_argument("--out", default="site")
    p.set_defaults(fn=cmd_site)

    sub.add_parser("mcp", help="run the MCP server (stdio)").set_defaults(fn=cmd_mcp)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    main()
