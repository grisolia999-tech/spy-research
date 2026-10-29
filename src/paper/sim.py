"""Simulated orders, fills, fees and slippage.

Fill convention (conservative, never midpoint):
  BUY  fills at ask + slippage_ticks * tick_size
  SELL fills at bid - slippage_ticks * tick_size (floored at tick_size)
Quantity is capped at displayed size on the side being hit; None is returned when no size.
A PaperFill is simulated data and is typed separately from an OptionQuote.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from data.quotes import OptionContract, OptionQuote

CONTRACT_MULTIPLIER = 100
FILL_KIND = "SIMULATED"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class FeeModel:
    per_contract: float = 0.0
    per_order: float = 0.0

    def cost(self, quantity: int) -> float:
        if quantity <= 0:
            return 0.0
        return round(self.per_contract * quantity + self.per_order, 4)


@dataclass(frozen=True)
class SlippageModel:
    ticks: int = 1
    tick_size: float = 0.01

    @property
    def amount(self) -> float:
        return round(self.ticks * self.tick_size, 4)


@dataclass(frozen=True)
class PaperOrder:
    contract: OptionContract
    side: OrderSide
    quantity: int
    created_at: datetime
    reason: str = ""


@dataclass(frozen=True)
class PaperFill:
    """A SIMULATED execution. price is per-share; premium is price * multiplier * quantity."""

    contract: OptionContract
    side: OrderSide
    quantity: int
    price: float
    fees: float
    filled_at: datetime
    quote_observed_at: datetime
    quote_source: str
    kind: str = FILL_KIND

    @property
    def gross_premium(self) -> float:
        return round(self.price * CONTRACT_MULTIPLIER * self.quantity, 4)


def simulate_fill(
    order: PaperOrder, quote: OptionQuote, fees: FeeModel, slippage: SlippageModel, now: datetime
) -> Optional[PaperFill]:
    """Simulate an executable fill from an observed quote. Returns None if unfillable."""
    if order.quantity <= 0 or not quote.is_two_sided:
        return None
    if order.side is OrderSide.BUY:
        size, price = quote.ask_size, quote.ask + slippage.amount
    else:
        size, price = quote.bid_size, max(quote.bid - slippage.amount, slippage.tick_size)
    quantity = min(order.quantity, size)
    if quantity <= 0:
        return None
    return PaperFill(
        contract=order.contract,
        side=order.side,
        quantity=quantity,
        price=round(price, 4),
        fees=fees.cost(quantity),
        filled_at=now,
        quote_observed_at=quote.observed_at,
        quote_source=quote.source,
    )


def simulate_buy_fill(contract, quantity, quote, fees, slippage, now) -> Optional[PaperFill]:
    return simulate_fill(PaperOrder(contract, OrderSide.BUY, quantity, now), quote, fees, slippage, now)


def simulate_sell_fill(contract, quantity, quote, fees, slippage, now) -> Optional[PaperFill]:
    return simulate_fill(PaperOrder(contract, OrderSide.SELL, quantity, now), quote, fees, slippage, now)


@dataclass(frozen=True)
class RoundTripResult:
    entry_premium: float      # gross dollars paid at entry, before fees
    exit_proceeds: float      # gross dollars received at exit, before fees
    total_fees: float
    net_pnl: float
    return_on_premium: float  # net_pnl / entry_premium


def round_trip_return(entry: PaperFill, exit_fill: PaperFill) -> RoundTripResult:
    """Net return on entry premium for a long position, from two simulated fills."""
    if entry.side is not OrderSide.BUY or exit_fill.side is not OrderSide.SELL:
        raise ValueError("round trip requires a BUY entry and a SELL exit")
    if entry.contract != exit_fill.contract or entry.quantity != exit_fill.quantity:
        raise ValueError("entry and exit must match contract and quantity")
    total_fees = round(entry.fees + exit_fill.fees, 4)
    net = round(exit_fill.gross_premium - entry.gross_premium - total_fees, 4)
    return RoundTripResult(
        entry_premium=entry.gross_premium,
        exit_proceeds=exit_fill.gross_premium,
        total_fees=total_fees,
        net_pnl=net,
        return_on_premium=round(net / entry.gross_premium, 6) if entry.gross_premium else 0.0,
    )
