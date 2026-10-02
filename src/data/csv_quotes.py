"""Manual CSV quote source.

A person types quotes they observe into a CSV during the session. Nothing here talks to a
broker or a data vendor. Each row is one observation; the latest row per contract wins.

Columns (header required, order free):
  time, expiry, strike, right, bid, ask, bid_size, ask_size, underlying
  time: "HH:MM:SS" or "HH:MM" in session_timezone on the expiry date, or a full ISO timestamp
  expiry: YYYY-MM-DD    strike: number    right: CALL or PUT
  bid/ask: dollars per share, blank if that side is missing

The source is labeled MANUAL_CSV so journals show these quotes were hand-entered.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from data.quotes import OptionContract, OptionQuote, Right

CSV_SOURCE = "MANUAL_CSV"
COLUMNS = ("time", "expiry", "strike", "right", "bid", "ask", "bid_size", "ask_size", "underlying")


def _parse_time(value: str, on_date: date, tz: ZoneInfo) -> datetime:
    value = value.strip()
    if "T" in value or " " in value:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=tz)
    parts = [int(x) for x in value.split(":")]
    while len(parts) < 3:
        parts.append(0)
    return datetime(on_date.year, on_date.month, on_date.day, *parts, tzinfo=tz)


def _opt_float(value: Optional[str]) -> Optional[float]:
    value = (value or "").strip()
    return float(value) if value else None


def parse_row(row: Dict[str, str], tz: ZoneInfo) -> OptionQuote:
    expiry = date.fromisoformat(row["expiry"].strip())
    contract = OptionContract("SPY", expiry, float(row["strike"]), Right(row["right"].strip().upper()))
    return OptionQuote(
        contract=contract,
        bid=_opt_float(row.get("bid")),
        ask=_opt_float(row.get("ask")),
        bid_size=int(float(row.get("bid_size") or 0)),
        ask_size=int(float(row.get("ask_size") or 0)),
        observed_at=_parse_time(row["time"], expiry, tz),
        source=CSV_SOURCE,
        underlying_price=_opt_float(row.get("underlying")),
    )


class CsvQuoteSource:
    """Re-reads the CSV on every latest() call so hand-typed rows are picked up immediately."""

    name = CSV_SOURCE

    def __init__(self, path: str | Path, session_timezone: str = "America/New_York") -> None:
        self.path = Path(path)
        self.tz = ZoneInfo(session_timezone)

    def _load(self) -> Dict[OptionContract, OptionQuote]:
        latest: Dict[OptionContract, OptionQuote] = {}
        if not self.path.exists():
            return latest
        with open(self.path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if not row.get("time") or not row.get("strike"):
                    continue
                try:
                    q = parse_row(row, self.tz)
                except (KeyError, ValueError):
                    continue  # skip malformed rows rather than crash the loop
                prev = latest.get(q.contract)
                if prev is None or q.observed_at >= prev.observed_at:
                    latest[q.contract] = q
        return latest

    def latest(self, contract: OptionContract) -> Optional[OptionQuote]:
        return self._load().get(contract)


def write_template(path: str | Path) -> None:
    """Write a header-only CSV so the person knows the columns."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(COLUMNS)
