"""Paper broker: same interface as the live one, fills come from paper.sim (ask/bid + slippage).

A limit BUY fills only if the simulated price <= limit; a limit SELL only if >= limit.
Otherwise the order rests as WORKING and is never filled later (no queue model). Say so when
reporting results. Fills are SIMULATED and never evidence of live profitability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from broker.interface import (BrokerPosition, OrderRequest, OrderResult, OrderState)
from data.quotes import OptionContract, OptionQuote
from paper.sim import FILL_KIND, FeeModel, OrderSide, PaperFill, SlippageModel, simulate_fill, PaperOrder


class PaperBroker:
    mode = "paper"

    def __init__(self, quote_for: Callable[[OptionContract], Optional[OptionQuote]],
                 fees: FeeModel, slippage: SlippageModel,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._quote_for, self._fees, self._slip, self._clock = quote_for, fees, slippage, clock
        self._orders: Dict[str, OrderResult] = {}
        self._pos: Dict[OptionContract, int] = {}
        self.fills: List[PaperFill] = []
        self._n = 0

    def place_order(self, req: OrderRequest) -> OrderResult:
        self._n += 1
        oid = f"PAPER-{self._n:06d}"
        now = self._clock()
        held = self._pos.get(req.contract, 0)
        if req.side is OrderSide.SELL and req.quantity > held:
            return self._store(OrderResult(oid, OrderState.REJECTED, message="sell exceeds paper position", kind=FILL_KIND))
        quote = self._quote_for(req.contract)
        fill = None if quote is None else simulate_fill(
            PaperOrder(req.contract, req.side, req.quantity, now, req.reason), quote, self._fees, self._slip, now)
        if fill is None:
            return self._store(OrderResult(oid, OrderState.WORKING, message="no executable quote; resting", kind=FILL_KIND))
        marketable = fill.price <= req.limit_price if req.side is OrderSide.BUY else fill.price >= req.limit_price
        if not marketable:
            return self._store(OrderResult(oid, OrderState.WORKING, message="limit not marketable; resting", kind=FILL_KIND))
        self.fills.append(fill)
        delta = fill.quantity if req.side is OrderSide.BUY else -fill.quantity
        self._pos[req.contract] = held + delta
        state = OrderState.FILLED if fill.quantity == req.quantity else OrderState.PARTIAL
        return self._store(OrderResult(oid, state, fill.quantity, fill.price, kind=FILL_KIND))

    def cancel_order(self, order_id: str) -> OrderResult:
        cur = self.order_status(order_id)
        if not cur.state.is_open:
            return cur
        return self._store(OrderResult(order_id, OrderState.CANCELED, cur.filled_quantity, cur.avg_fill_price, kind=FILL_KIND))

    def cancel_all(self) -> List[str]:
        ids = [o.order_id for o in self.open_orders()]
        for i in ids:
            self.cancel_order(i)
        return ids

    def order_status(self, order_id: str) -> OrderResult:
        return self._orders.get(order_id) or OrderResult(order_id, OrderState.UNKNOWN, kind=FILL_KIND)

    def open_orders(self) -> List[OrderResult]:
        return [o for o in self._orders.values() if o.state.is_open]

    def positions(self) -> List[BrokerPosition]:
        return [BrokerPosition(c, q) for c, q in self._pos.items() if q > 0]

    def _store(self, r: OrderResult) -> OrderResult:
        self._orders[r.order_id] = r
        return r
