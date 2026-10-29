"""Trading engine: one tick = refresh data, watch login, run ORB, manage one position.

Data and signals run in every mode (including OFF). Orders only go out through RoutedBroker, which
applies the switch and limits. States: FLAT -> ENTRY_WORKING -> LONG -> EXIT_WORKING -> DONE (one
round trip per day). A blocked or unfilled entry consumes the day's signal (no chasing).
Deterministic Python only: no model calls in this loop.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from broker.interface import OrderBlocked, OrderRequest, OrderState
from data.quotes import OptionContract
from paper.sim import FeeModel, OrderSide, PaperFill, SlippageModel
from schwabapi.auth import SchwabLoginRequired, SchwabTemporaryError
from signals.orb import OrbParams, orb_signal
from signals.rules import Action, ExitParams, ProfitTargetExit, parse_hhmm


class AgeBox:
    """Holds the data age the gate reads at the moment an order is evaluated."""
    def __init__(self) -> None:
        self.value: Optional[float] = None

    def get(self) -> Optional[float]:
        return self.value


class Engine:
    def __init__(self, cfg: dict, system, quotes, bars, watch, notifier, age: AgeBox, clock) -> None:
        self.cfg, self.sys, self.quotes, self.bars = cfg, system, quotes, bars
        self.watch, self.notifier, self.age, self.clock = watch, notifier, age, clock
        self.tz = ZoneInfo(cfg.get("session_timezone", "America/New_York"))
        orb = cfg["orb"]
        self.orb = OrbParams.from_config(orb)
        self.max_signal_age = float(orb.get("max_signal_age_seconds", 120))
        self.order_timeout = float(orb.get("order_timeout_seconds", 30))
        self.qty = int(cfg.get("max_contracts_per_trade", 1))
        self.fees = FeeModel(float(cfg["fees"]["per_contract"]), float(cfg["fees"].get("per_order", 0.0)))
        self.slip = SlippageModel(int(cfg.get("slippage_ticks", 1)), float(cfg.get("tick_size", 0.01)))
        ex = orb["exit"]
        self.exit_rule = ProfitTargetExit(ExitParams(float(ex["profit_target_net"]), float(ex["soft_stop_fraction"]),
                                                     parse_hhmm(ex["latest_exit_time"]), self.tz.key), self.fees, self.slip)
        self.day = None
        self._reset()

    def _reset(self) -> None:
        self.state, self.signal, self.contract = "FLAT", None, None
        self.entered_qty = 0
        self.order_id, self.order_mode, self.order_at = None, None, None
        self.entry_price, self.entry_time, self.held = None, None, 0
        self.exit_proceeds, self.exit_fees, self.entry_fees, self.warned_off = 0.0, 0.0, 0.0, False

    # -- tick -----------------------------------------------------------------------
    def run_once(self) -> str:
        now = self.clock()
        today = now.astimezone(self.tz).date()
        if self.day != today:
            if self.state in ("ENTRY_WORKING", "LONG", "EXIT_WORKING"):
                self.sys.ledger.append("day_rollover_with_open_state", state=self.state)
                self.notifier.send("Trading", "New day with an open position/order. Check Schwab.")
            self.day = today
            self._reset()
            self.bars.bars.clear()
            self.bars._cur = None
        if self.watch.check(now) == "expired":
            return "expired"
        try:
            self.quotes.refresh()
        except SchwabLoginRequired as e:
            self.sys.halt(f"login required: {e}")
            self.notifier.send("Trading HALTED", "Schwab login rejected. Switch OFF. Run ./reauth.")
            return "login"
        except SchwabTemporaryError as e:
            self.sys.ledger.append("data_error", error=str(e))
            return "data_error"
        if self.quotes.underlying_last is not None and self.quotes.underlying_observed_at is not None:
            self.bars.add(self.quotes.underlying_observed_at, self.quotes.underlying_last)
        mode = self.sys.switch.mode()
        step = {"FLAT": self._flat, "ENTRY_WORKING": self._entry_working, "LONG": self._long,
                "EXIT_WORKING": self._exit_working}.get(self.state)
        if step:
            step(now, mode)
        return self.state

    def _data_age(self, now, contract=None) -> Optional[float]:
        ages = []
        u = self.quotes.underlying_observed_at
        ages.append(None if u is None else (now - u).total_seconds())
        if contract is not None:
            q = self.quotes.latest(contract)
            ages.append(None if q is None else q.age_seconds(now))
        return None if any(a is None for a in ages) else max(ages)

    # -- states ---------------------------------------------------------------------
    def _flat(self, now, mode) -> None:
        if self.signal is None:
            self.signal = orb_signal(self.bars.completed(now), self.day, self.orb)
            if self.signal is None:
                return
            self.sys.ledger.append("signal", mode=mode, rule="ORB", right=self.signal.right.value,
                                   price=self.signal.trigger_price, size=0,
                                   range=[self.signal.range_low, self.signal.range_high],
                                   trigger_time=self.signal.trigger_close_time.isoformat(), checks={})
        if mode == "off":
            return  # data and signals keep running; no orders
        if (now - self.signal.trigger_close_time).total_seconds() > self.max_signal_age:
            self.sys.ledger.append("signal_expired", mode=mode, reason="armed too late for this trigger", checks={})
            self.state = "DONE"
            return
        c = OptionContract(self.quotes.underlying, self.day, float(round(self.signal.trigger_price)), self.signal.right)
        if c not in self.quotes.contracts:
            self.quotes.contracts.append(c)
            try:
                self.quotes.refresh()
            except (SchwabTemporaryError, SchwabLoginRequired):
                return
        q = self.quotes.latest(c)
        if q is None or not q.is_two_sided:
            return  # wait for a tradable quote while the signal is fresh
        self.contract = c
        self.age.value = self._data_age(now, c)
        price = round(q.ask + self.slip.amount, 4)
        res = self._place(OrderRequest(c, OrderSide.BUY, self.qty, price, f"ORB {self.signal.right.value} break"), mode)
        if res is None:
            self.state = "DONE"  # blocked/failed attempt consumes the signal
            return
        self.order_id, self.order_mode, self.order_at, self.state = res.order_id, mode, now, "ENTRY_WORKING"
        self._after_fill_check(res, now, entering=True)

    def _entry_working(self, now, mode) -> None:
        res = self._broker(self.order_mode).order_status(self.order_id)
        self._after_fill_check(res, now, entering=True)

    def _after_fill_check(self, res, now, entering: bool) -> None:
        b = self._broker(self.order_mode)
        if res.state is OrderState.FILLED or (res.state is OrderState.PARTIAL and res.filled_quantity):
            if res.state is OrderState.PARTIAL:
                try:
                    b.cancel_order(self.order_id)
                except Exception as e:
                    self.sys.ledger.append("cancel_error", order_id=self.order_id, error=str(e))
            self._enter_long(res.filled_quantity, res.avg_fill_price, now)
            return
        if res.state in (OrderState.REJECTED, OrderState.CANCELED, OrderState.EXPIRED):
            self.state = "DONE"
            return
        if (now - self.order_at).total_seconds() > self.order_timeout:
            try:
                b.cancel_order(self.order_id)
            except Exception as e:
                self.sys.ledger.append("cancel_error", order_id=self.order_id, error=str(e))
            final = b.order_status(self.order_id)
            if final.filled_quantity:
                self._enter_long(final.filled_quantity, final.avg_fill_price, now)
            else:
                self.sys.ledger.append("entry_unfilled", order_id=self.order_id, mode=self.order_mode, checks={})
                self.state = "DONE"

    def _long(self, now, mode) -> None:
        q = self.quotes.latest(self.contract)
        fill = PaperFill(self.contract, OrderSide.BUY, self.held, self.entry_price, self.entry_fees, self.entry_time,
                         self.entry_time, self.order_mode or "", kind="BROKER" if self.order_mode == "live" else "SIMULATED")
        sig = self.exit_rule.evaluate(fill, q, now)
        if sig.action is not Action.EXIT:
            return
        if mode == "off":
            if not self.warned_off:
                self.warned_off = True
                self.sys.ledger.append("position_open_while_off", contract=self.contract.symbol(), size=self.held,
                                       reason=sig.reason, checks={})
                self.notifier.send("Trading", "Exit due but switch is OFF. Close the position manually.")
            return
        if q is None or not q.is_two_sided:
            return
        self.age.value = self._data_age(now, self.contract)
        price = max(round(q.bid - self.slip.amount, 4), self.slip.tick_size)
        res = self._place(OrderRequest(self.contract, OrderSide.SELL, self.held, price, f"EXIT: {sig.reason}"), mode)
        if res is None:
            return  # blocked or failed: stay LONG, retry next tick
        self.order_id, self.order_mode, self.order_at, self.state = res.order_id, mode, now, "EXIT_WORKING"
        self._exit_progress(res, now)

    def _exit_working(self, now, mode) -> None:
        self._exit_progress(self._broker(self.order_mode).order_status(self.order_id), now)

    def _exit_progress(self, res, now) -> None:
        b = self._broker(self.order_mode)
        if res.state is OrderState.WORKING and (now - self.order_at).total_seconds() > self.order_timeout:
            try:
                b.cancel_order(self.order_id)
            except Exception as e:
                self.sys.ledger.append("cancel_error", order_id=self.order_id, error=str(e))
            res = b.order_status(self.order_id)
            if res.state.is_open:
                res = type(res)(res.order_id, OrderState.CANCELED, res.filled_quantity, res.avg_fill_price, res.message, res.kind)
        if res.filled_quantity:
            self.exit_proceeds += res.filled_quantity * (res.avg_fill_price or 0) * 100
            self.exit_fees += self.fees.cost(res.filled_quantity)
            self.held -= res.filled_quantity
        if res.state.is_open:
            return
        if self.held <= 0:
            net = self.exit_proceeds - self.exit_fees - self.entry_price * 100 * self.entered_qty - self.entry_fees
            self.sys.gate.record_realized(round(net, 4))
            self.sys.ledger.append("round_trip", mode=self.order_mode, contract=self.contract.symbol(), size=self.entered_qty,
                                   price=self.entry_price, net_pnl=round(net, 4), checks={},
                                   note="SIMULATED" if self.order_mode == "paper" else "BROKER fills")
            self.state = "DONE"
        else:
            self.state = "LONG"  # partial or cancelled exit: re-evaluate next tick

    def _enter_long(self, qty, price, now) -> None:
        self.held = self.entered_qty = qty
        self.entry_price, self.entry_time = price, now
        self.entry_fees = self.fees.cost(qty)
        self.state = "LONG"

    # -- helpers --------------------------------------------------------------------
    def _broker(self, mode):
        return self.sys.live if mode == "live" else self.sys.paper

    def _place(self, req, mode):
        try:
            return self.sys.broker.place_order(req)
        except OrderBlocked:
            return None
        except Exception as e:
            self.notifier.send("Trading", f"Order error: {e}")
            return None
