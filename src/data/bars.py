"""Underlying price bars (OBSERVED history). Loader for TradingView CSV exports."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Bar:
    start: datetime   # bar OPEN time, tz-aware
    open: float
    high: float
    low: float
    close: float


def load_tradingview_csv(path, tz: str = "America/New_York") -> List[Bar]:
    zone = ZoneInfo(tz)
    out = []
    with open(Path(path), newline="") as f:
        for r in csv.DictReader(f):
            out.append(Bar(datetime.fromtimestamp(int(r["time"]), zone), float(r["open"]),
                           float(r["high"]), float(r["low"]), float(r["close"])))
    out.sort(key=lambda b: b.start)
    return out


def bar_length(bars: List[Bar]) -> timedelta:
    gaps = sorted({(b.start - a.start) for a, b in zip(bars, bars[1:]) if b.start > a.start})
    if not gaps:
        raise ValueError("need at least two bars to infer bar length")
    return gaps[0]
