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


@dataclass(frozen=True)
class ExitParams:
    profit_target: float          # fraction of entry premium, e.g. 0.15
    max_loss_per_trade: float     # dollars, positive number
    latest_exit_time: time        # wall-clock in session_timezone
    session_timezone: str = "America/New_York"


class ProfitTargetExit:
    """Deterministic exit: profit target, max loss, or latest exit time.

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
        if result.net_pnl <= -self.params.max_loss_per_trade:
            return Signal(
                Action.EXIT, self.rule_id,
                f"max loss {self.params.max_loss_per_trade:.2f} reached (net {result.net_pnl:.2f})",
                entry.quantity,
            )
        return Signal(Action.NONE, self.rule_id, f"hold (net {result.return_on_premium:.2%})")
