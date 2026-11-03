"""Daily price providers. Grading only needs daily OHLC bars."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float


class PriceProvider(Protocol):
    def daily_bars(self, ticker: str, start: date, end: date) -> list[Bar]:
        """Bars with start <= day <= end, oldest first."""


class YFinanceProvider:
    """Free Yahoo data via yfinance (``pip install thesis-ledger[yfinance]``).

    Prices are used only to grade outcomes; they are never written to the
    public site, which keeps redistribution of vendor data out of the picture.
    """

    def __init__(self) -> None:
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("yfinance is not installed: pip install 'thesis-ledger[yfinance]'") from exc
        self._cache: dict[tuple[str, date, date], list[Bar]] = {}

    def daily_bars(self, ticker: str, start: date, end: date) -> list[Bar]:  # pragma: no cover - network
        import yfinance as yf

        key = (ticker, start, end)
        if key not in self._cache:
            df = yf.Ticker(ticker).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                            interval="1d", auto_adjust=False)
            self._cache[key] = [
                Bar(idx.date(), float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"]))
                for idx, r in df.iterrows()
            ]
        return self._cache[key]


class CSVProvider:
    """Reads ``<dir>/<TICKER>.csv`` with columns date,open,high,low,close."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def daily_bars(self, ticker: str, start: date, end: date) -> list[Bar]:
        path = self.dir / f"{ticker.upper()}.csv"
        if not path.exists():
            return []
        out = []
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                d = date.fromisoformat(row["date"][:10])
                if start <= d <= end:
                    out.append(Bar(d, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])))
        return sorted(out, key=lambda b: b.day)


class StaticProvider:
    """In-memory bars, for tests and demos."""

    def __init__(self, bars: dict[str, list[Bar]]):
        self.bars = {k.upper(): sorted(v, key=lambda b: b.day) for k, v in bars.items()}

    def daily_bars(self, ticker: str, start: date, end: date) -> list[Bar]:
        return [b for b in self.bars.get(ticker.upper(), []) if start <= b.day <= end]
