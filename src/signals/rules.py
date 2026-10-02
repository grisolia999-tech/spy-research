"""Interfaces for deterministic entry and exit rules.

Rules are pure functions of (quote, context). They must not call external services, use
randomness, or consult a model. The only entry rule shipped is NoEntryRule, which never
enters: no strategy is proposed by this scaffold.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional, Protocol
from zoneinfo import ZoneInfo

from data.quotes import OptionQuote
from paper.sim import FeeModel, PaperFill, SlippageModel, round_trip_return, simulate_sell_fill


class Action(str, Enum):
    NONE = "NONE"
    ENTER_LONG = "ENTER_LONG"
    EXIT = "EXIT"


@dataclass(frozen=True)
class Signal:
    action: Action
    rule_id: str
    reason: str
    quantity: int = 0


class EntryRule(Protocol):
    rule_id: str

    def evaluate(self, quote: OptionQuote, now: datetime) -> Signal: ...


class NoEntryRule:
    """Default entry rule: never enters. Replace only with a configured, reviewed rule."""

    rule_id = "NO_ENTRY_RULE"

    def evaluate(self, quote: OptionQuote, now: datetime) -> Signal:
        return Signal(Action.NONE, self.rule_id, "entry rules not configured")


def parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


@dataclass(frozen=True)
class LiquidityWindowParams:
    """Rule A parameters. All are placeholders to be set in config; none is a tuned value."""

    entry_start: time
    entry_end: time
    min_bid_size: int
    min_ask_size: int
    min_ask: float
    max_ask: float
    session_timezone: str = "America/New_York"

    @classmethod
    def from_dict(cls, d: dict) -> "LiquidityWindowParams":
        return cls(
            entry_start=parse_hhmm(d["entry_start"]),
            entry_end=parse_hhmm(d["entry_end"]),
            min_bid_size=int(d["min_bid_size"]),
            min_ask_size=int(d["min_ask_size"]),
            min_ask=float(d["min_ask"]),
            max_ask=float(d["max_ask"]),
            session_timezone=str(d.get("session_timezone", "America/New_York")),
        )


class LiquidityWindowEntry:
    """Rule A: time-window liquidity gate with a premium band.

    Enters long when `now` is inside [entry_start, entry_end) in the session timezone, the
    quote is two-sided, both displayed sizes meet minimums, and the ask lies inside
    [min_ask, max_ask]. The premium band exists because round-trip friction (fees, spread,
    slippage) is a larger share of cheap premiums, which makes a net 15% target unreachable.

    This rule has NO directional opinion and is NOT a profitability signal. It only limits
    paper entries to conditions where simulated fills are least unrealistic.
    """

    rule_id = "LIQUIDITY_WINDOW_A"

    def __init__(self, params: LiquidityWindowParams) -> None:
        self.params = params

    def evaluate(self, quote: OptionQuote, now: datetime) -> Signal:
        p = self.params
        local = now.astimezone(ZoneInfo(p.session_timezone)).time()
        if not (p.entry_start <= local < p.entry_end):
            return Signal(Action.NONE, self.rule_id, f"outside entry window {p.entry_start:%H:%M}-{p.entry_end:%H:%M}")
        if not quote.is_two_sided:
            return Signal(Action.NONE, self.rule_id, "quote not two-sided")
        if quote.bid_size < p.min_bid_size or quote.ask_size < p.min_ask_size:
            return Signal(Action.NONE, self.rule_id, f"size {quote.bid_size}x{quote.ask_size} below {p.min_bid_size}x{p.min_ask_size}")
        if quote.ask < p.min_ask or quote.ask > p.max_ask:
            return Signal(Action.NONE, self.rule_id, f"ask {quote.ask:.2f} outside premium band {p.min_ask:.2f}-{p.max_ask:.2f}")
        return Signal(Action.ENTER_LONG, self.rule_id, "window, sizes and premium band satisfied", 1)


@dataclass(frozen=True)
class ExitParams:
    profit_target: float          # fraction of entry premium, e.g. 0.15
    stop_loss_fraction: float     # soft stop: exit when net return <= -fraction, e.g. 0.50
    latest_exit_time: time        # wall-clock in session_timezone
    session_timezone: str = "America/New_York"


class ProfitTargetExit:
    """Deterministic exit: profit target, soft stop, or latest exit time.

    The soft stop is not a guarantee. A 0DTE option can gap through it between quotes. The
    hard maximum loss for a long option is the premium paid, enforced at entry by risk.

    The profit check uses a SIMULATED sell fill at the bid minus slippage, net of fees.
    It never uses the midpoint.
    """

    rule_id = "EXIT_TARGET_LOSS_TIME"

    def __init__(self, params: ExitParams, fees: FeeModel, slippage: SlippageModel) -> None:
        self.params = params
        self.fees = fees
        self.slippage = slippage

    def evaluate(self, entry: PaperFill, quote: Optional[OptionQuote], now: datetime) -> Signal:
        tz = ZoneInfo(self.params.session_timezone)
        if now.astimezone(tz).time() >= self.params.latest_exit_time:
            return Signal(Action.EXIT, self.rule_id, "latest exit time reached", entry.quantity)
        if quote is None or not quote.is_two_sided:
            return Signal(Action.NONE, self.rule_id, "no two-sided quote; cannot evaluate exit")
        exit_fill = simulate_sell_fill(entry.contract, entry.quantity, quote, self.fees, self.slippage, now)
        if exit_fill is None:
            return Signal(Action.NONE, self.rule_id, "no bid size; cannot simulate exit")
        result = round_trip_return(entry, exit_fill)
        if result.return_on_premium >= self.params.profit_target:
            return Signal(
                Action.EXIT, self.rule_id,
                f"profit target {self.params.profit_target:.2%} reached (net {result.return_on_premium:.2%})",
                entry.quantity,
            )
        if result.return_on_premium <= -self.params.stop_loss_fraction:
            return Signal(
                Action.EXIT, self.rule_id,
                f"soft stop {self.params.stop_loss_fraction:.0%} reached (net {result.return_on_premium:.2%})",
                entry.quantity,
            )
        return Signal(Action.NONE, self.rule_id, f"hold (net {result.return_on_premium:.2%})")
