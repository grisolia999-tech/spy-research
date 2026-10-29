"""Order gate: switch + limits enforced before EVERY order, in every mode.

Checks (BUY = opening order; SELL = closing order):
  switch_not_off, owner_reviewed (live only), data_fresh, size, position, cost (BUY),
  orders_per_day (BUY), daily_loss (BUY), trading_window (BUY).
Closing SELLs skip window/count/loss/cost so a limit can never trap an open position; they still
need the switch on, fresh data, and a position to close.
Hitting max_daily_loss calls `on_halt` (switch off + cancel all) and blocks every BUY.
Each attempt, pass or block, is written to the ledger with the full check results.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from broker.interface import (Broker, BrokerPosition, OrderBlocked, OrderRequest, OrderResult)
from control.limits import Limits
from control.ledger import Ledger
from control.switch import Switch
from paper.sim import CONTRACT_MULTIPLIER, OrderSide


class DayState:
    """Counts orders and realized P&L for one trading day (resets when the date changes)."""

    def __init__(self) -> None:
        self.day: Optional[date] = None
        self.orders = 0
        self.realized = 0.0

    def roll(self, today: date) -> None:
        if self.day != today:
            self.day, self.orders, self.realized = today, 0, 0.0


class OrderGate:
    def __init__(self, switch: Switch, limits: Callable[[], Limits], ledger: Ledger, state: DayState,
                 data_age: Callable[[], Optional[float]], held: Callable[[object], int],
                 on_halt: Callable[[str], None], clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.switch, self._limits, self.ledger, self.state = switch, limits, ledger, state
        self._age, self._held, self._on_halt, self._clock = data_age, held, on_halt, clock

    def evaluate(self, req: OrderRequest, mode: str) -> Dict[str, bool]:
        lim = self._limits()
        now = self._clock().astimezone(ZoneInfo(lim.timezone))
        self.state.roll(now.date())
        age = self._age()
        opening = req.side is OrderSide.BUY
        held = self._held(req.contract)
        checks = {
            "switch_not_off": mode in ("paper", "live"),
            "owner_reviewed": lim.owner_reviewed or mode != "live",
            "data_fresh": age is not None and age <= lim.max_data_age_seconds,
            "size": req.quantity <= lim.max_contracts_per_order,
        }
        if opening:
            cost = req.limit_price * CONTRACT_MULTIPLIER * req.quantity
            checks.update({
                "position": held + req.quantity <= lim.max_position_contracts,
                "cost": cost <= lim.max_order_cost_usd,
                "orders_per_day": self.state.orders < lim.max_orders_per_day,
                "daily_loss": self.state.realized > -lim.max_daily_loss_usd,
                "trading_window": lim.window_start <= now.time() <= lim.window_end,
            })
        else:
            checks["has_position"] = req.quantity <= held
        return checks

    def check(self, req: OrderRequest, mode: str) -> Dict[str, bool]:
        try:
            checks = self.evaluate(req, mode)
        except Exception as e:  # limits unreadable etc. => block, and say why
            self._log("order_blocked", req, mode, {"limits_loaded": False}, error=str(e))
            raise OrderBlocked(f"limits unavailable: {e}") from e
        failed = [k for k, v in checks.items() if not v]
        if failed:
            self._log("order_blocked", req, mode, checks, failed=failed)
            if "daily_loss" in failed:
                self._safe_halt("max daily loss reached")
            raise OrderBlocked("blocked by: " + ", ".join(failed))
        if req.side is OrderSide.BUY:
            self.state.orders += 1
        self._log("order_attempt", req, mode, checks)
        return checks

    def record_realized(self, pnl: float) -> None:
        self.state.roll(self._clock().astimezone(ZoneInfo(self._limits().timezone)).date())
        self.state.realized += pnl
        if self.state.realized <= -self._limits().max_daily_loss_usd:
            self._safe_halt("max daily loss reached")

    def _safe_halt(self, reason: str) -> None:
        try:
            self._on_halt(reason)
        except Exception as e:  # the switch is already off; record that the cancel step failed
            self.ledger.append("halt_error", reason=reason, error=str(e))

    def _log(self, event: str, req: OrderRequest, mode: str, checks, **extra) -> None:
        self.ledger.append(event, mode=mode, signal=req.reason, contract=req.contract.symbol(),
                           side=req.side.value, price=req.limit_price, size=req.quantity,
                           checks=checks, **extra)


class RoutedBroker:
    """The only broker the engine talks to. Routes by the switch; gates every order."""

    def __init__(self, switch: Switch, gate: OrderGate, paper: Broker, live: Broker, ledger: Ledger) -> None:
        self.switch, self.gate, self.paper, self.live, self.ledger = switch, gate, paper, live, ledger

    @property
    def mode(self) -> str:
        return self.switch.mode()

    def place_order(self, req: OrderRequest) -> OrderResult:
        mode = self.switch.mode()
        self.gate.check(req, mode)  # raises OrderBlocked when off or any limit fails
        broker = self.live if mode == "live" else self.paper
        try:
            res = broker.place_order(req)
        except Exception as e:
            self.ledger.append("order_error", mode=mode, signal=req.reason, contract=req.contract.symbol(),
                               side=req.side.value, price=req.limit_price, size=req.quantity, error=str(e))
            raise
        self.ledger.append("order_result", mode=mode, signal=req.reason, contract=req.contract.symbol(),
                           side=req.side.value, price=res.avg_fill_price or req.limit_price,
                           size=res.filled_quantity or req.quantity, order_id=res.order_id,
                           state=res.state.value, kind=res.kind, message=res.message)
        return res

    def cancel_all(self) -> List[str]:
        ids: List[str] = []
        errors: List[str] = []
        for name, b in (("paper", self.paper), ("live", self.live)):
            try:
                ids += b.cancel_all()
            except Exception as e:
                errors.append(f"{name}: {e}")
        self.ledger.append("cancel_all", cancelled=ids, errors=errors)
        if errors:
            raise OrderBlocked("cancel_all incomplete: " + "; ".join(errors))
        return ids

    def positions(self) -> List[BrokerPosition]:
        return (self.live if self.switch.mode() == "live" else self.paper).positions()

    def order_status(self, order_id: str) -> OrderResult:
        return (self.live if self.switch.mode() == "live" else self.paper).order_status(order_id)

    def open_orders(self) -> List[OrderResult]:
        return self.paper.open_orders() + self.live.open_orders()
