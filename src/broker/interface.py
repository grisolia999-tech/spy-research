"""Broker interface shared by the paper and Schwab implementations.

Only limit orders for single option legs (long calls/puts: buy to open, sell to close).
Every broker returns the same types, so the engine does not care which one is behind it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Protocol

from data.quotes import OptionContract
from paper.sim import OrderSide


class BrokerError(Exception):
    """Broker call failed in a way the caller must see (never swallowed)."""


class OrderBlocked(BrokerError):
    """The order gate (switch/limits) refused to let an order out. Nothing was sent."""


class OrderState(str, Enum):
    WORKING = "WORKING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_open(self) -> bool:
        return self in (OrderState.WORKING, OrderState.PARTIAL)


@dataclass(frozen=True)
class OrderRequest:
    contract: OptionContract
    side: OrderSide
    quantity: int
    limit_price: float
    reason: str = ""

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.limit_price <= 0:
            raise ValueError("limit_price must be positive")


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    state: OrderState
    filled_quantity: int = 0
    avg_fill_price: Optional[float] = None
    message: str = ""
    kind: str = ""          # "SIMULATED" for the paper broker, "BROKER" for Schwab


@dataclass(frozen=True)
class BrokerPosition:
    contract: OptionContract
    quantity: int           # long contracts


class Broker(Protocol):
    mode: str               # "paper" or "live"

    def place_order(self, req: OrderRequest) -> OrderResult: ...
    def cancel_order(self, order_id: str) -> OrderResult: ...
    def cancel_all(self) -> List[str]: ...
    def order_status(self, order_id: str) -> OrderResult: ...
    def open_orders(self) -> List[OrderResult]: ...
    def positions(self) -> List[BrokerPosition]: ...
