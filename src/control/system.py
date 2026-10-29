"""Wires switch, limits, ledger, gate and brokers together. Used by the engine, ./arm and ./halt."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from broker.interface import Broker, OrderBlocked
from broker.paper_orders import PaperBroker
from control.gate import DayState, OrderGate, RoutedBroker
from control.ledger import Ledger
from control.limits import Limits
from control.switch import Switch
from paper.sim import FeeModel, SlippageModel


@dataclass
class System:
    root: Path
    switch: Switch
    ledger: Ledger
    gate: OrderGate
    broker: RoutedBroker
    paper: PaperBroker
    live: Broker

    def halt(self, reason: str) -> list:
        """Switch off first, then cancel everything. Never raises before the switch is off."""
        self.switch.set("off", "system", f"HALT: {reason}")
        return self.broker.cancel_all()


def build(root, live_broker: Optional[Broker] = None,
          quote_for: Callable = lambda c: None, data_age: Callable = lambda: None,
          fees: FeeModel = FeeModel(0.65), slippage: SlippageModel = SlippageModel(1),
          clock=None) -> System:
    root = Path(root)
    kw = {"clock": clock} if clock else {}
    ledger = Ledger(root / "logs" / "trades.jsonl", **kw)
    switch = Switch(root / "config" / "switch.json", ledger, **kw)
    paper = PaperBroker(quote_for, fees, slippage, **kw)
    holder = {}

    def limits() -> Limits:
        return Limits.load(root / "config" / "limits.json")

    def held(contract) -> int:
        b = holder["routed"]
        return sum(p.quantity for p in b.positions() if p.contract == contract)

    def on_halt(reason: str) -> None:
        holder["system"].halt(reason)

    gate = OrderGate(switch, limits, ledger, DayState(), data_age, held, on_halt, **kw)
    if live_broker is None:
        live_broker = _NoLive()
    routed = RoutedBroker(switch, gate, paper, live_broker, ledger)
    holder["routed"] = routed
    system = System(root, switch, ledger, gate, routed, paper, live_broker)
    holder["system"] = system
    return system


class _NoLive:
    """Placeholder when no Schwab connection is configured: live orders are impossible."""
    mode = "live"

    def place_order(self, req):
        raise OrderBlocked("no live broker configured")

    def cancel_order(self, order_id):
        raise OrderBlocked("no live broker configured")

    def cancel_all(self):
        return []

    def order_status(self, order_id):
        raise OrderBlocked("no live broker configured")

    def open_orders(self):
        return []

    def positions(self):
        return []
