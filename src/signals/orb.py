"""Opening Range Breakout (ORB) on the underlying. Deterministic; no model, no randomness.

Rules, all parameters from config/paper.json -> "orb":
  1. Opening range = high/low of bars that OPEN in [or_start, or_end).
  2. Only completed bars count: a bar's close is known at start + bar length (no lookahead).
  3. Trigger = first completed bar whose close time is in [entry_start, entry_end] and whose close is
     above the range high (CALL) or below the range low (PUT). One trigger per session.
  4. Missing opening-range bars (gap, late data) => no signal.
The option contract choice (nearest ATM, same-day expiry) and exits are in the config and applied
by the engine; this module only decides direction and the trigger bar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import List, Optional

from data.bars import Bar, bar_length
from data.quotes import Right


@dataclass(frozen=True)
class OrbParams:
    or_start: time
    or_end: time
    entry_start: time
    entry_end: time
    min_range_dollars: float = 0.0

    @classmethod
    def from_config(cls, cfg: dict) -> "OrbParams":
        t = lambda s: time(int(s[:2]), int(s[3:5]))
        return cls(t(cfg["or_start"]), t(cfg["or_end"]), t(cfg["entry_start"]), t(cfg["entry_end"]),
                   float(cfg.get("min_range_dollars", 0.0)))


@dataclass(frozen=True)
class OrbSignal:
    right: Right
    trigger_close_time: datetime
    trigger_price: float
    range_high: float
    range_low: float


def orb_signal(bars: List[Bar], day: date, p: OrbParams) -> Optional[OrbSignal]:
    day_bars = [b for b in bars if b.start.date() == day]
    if len(day_bars) < 2:
        return None
    step = bar_length(day_bars)
    or_bars = [b for b in day_bars if p.or_start <= b.start.time() < p.or_end]
    expected = int((datetime.combine(day, p.or_end) - datetime.combine(day, p.or_start)) / step)
    if expected <= 0 or len(or_bars) != expected:
        return None
    hi, lo = max(b.high for b in or_bars), min(b.low for b in or_bars)
    if hi - lo < p.min_range_dollars:
        return None
    for b in day_bars:
        closed_at = b.start + step
        if closed_at.time() < max(p.entry_start, p.or_end) or closed_at.time() > p.entry_end:
            continue
        if b.close > hi:
            return OrbSignal(Right.CALL, closed_at, b.close, hi, lo)
        if b.close < lo:
            return OrbSignal(Right.PUT, closed_at, b.close, hi, lo)
    return None
