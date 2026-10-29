import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from broker.interface import OrderBlocked, OrderRequest, OrderState
from control.ledger import Ledger
from control.limits import Limits, LimitsError
from control.switch import Switch, SwitchError
from control.system import build
from data.quotes import OptionContract, OptionQuote, Right
from paper.sim import OrderSide

C = OptionContract("SPY", date(2026, 10, 30), 500.0, Right.CALL)
NY = ZoneInfo("America/New_York")
LIMITS = {"owner_reviewed": True, "max_contracts_per_order": 1, "max_position_contracts": 1,
          "max_order_cost_usd": 150, "max_orders_per_day": 2, "max_daily_loss_usd": 100,
          "trading_window": {"start": "09:36", "end": "15:30", "timezone": "America/New_York"},
          "max_data_age_seconds": 5}


class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t.astimezone(timezone.utc)


class Env:
    def __init__(self, tc, limits=None, age=1.0):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "config").mkdir()
        (self.tmp / "config" / "limits.json").write_text(json.dumps(limits or LIMITS))
        self.clock = Clock(datetime(2026, 10, 30, 10, 0, tzinfo=NY))
        self.age = age
        q = OptionQuote(C, 1.00, 1.05, 10, 10, self.clock(), "T")
        self.sys = build(self.tmp, quote_for=lambda c: q, data_age=lambda: self.age, clock=self.clock)
        tc.addCleanup(lambda: None)

    def arm(self, mode, live_until=None):
        self.sys.switch.set(mode, "owner", "test", valid_until=live_until)

    def buy(self, qty=1, price=1.10):
        return self.sys.broker.place_order(OrderRequest(C, OrderSide.BUY, qty, price, "test-signal"))

    def sell(self, qty=1, price=0.90):
        return self.sys.broker.place_order(OrderRequest(C, OrderSide.SELL, qty, price, "test-exit"))


class SwitchTests(unittest.TestCase):
    def test_default_off_and_corrupt_is_off(self):
        e = Env(self)
        self.assertEqual(e.sys.switch.mode(), "off")
        (e.tmp / "config" / "switch.json").write_text("{nonsense")
        self.assertEqual(e.sys.switch.mode(), "off")

    def test_system_cannot_enable(self):
        e = Env(self)
        with self.assertRaises(SwitchError):
            e.sys.switch.set("paper", "system")
        with self.assertRaises(SwitchError):
            e.sys.switch.set("live", "agent", valid_until=e.clock() + timedelta(hours=1))

    def test_live_expires(self):
        e = Env(self)
        e.arm("live", e.clock() + timedelta(hours=1))
        self.assertEqual(e.sys.switch.mode(), "live")
        e.clock.t += timedelta(hours=2)
        self.assertEqual(e.sys.switch.mode(), "off")

    def test_reset_to_off_logs(self):
        e = Env(self)
        e.arm("paper")
        e.sys.switch.reset_to_off("startup")
        self.assertEqual(e.sys.switch.mode(), "off")
        self.assertEqual(e.sys.ledger.verify(), None)
        self.assertEqual([r["event"] for r in e.sys.ledger.records()], ["switch_change", "switch_change"])


class GateTests(unittest.TestCase):
    def test_off_blocks_and_logs(self):
        e = Env(self)
        with self.assertRaises(OrderBlocked):
            e.buy()
        r = list(e.sys.ledger.records())[-1]
        self.assertEqual((r["event"], r["mode"], r["checks"]["switch_not_off"]), ("order_blocked", "off", False))

    def test_paper_buy_fills_and_is_logged_with_checks(self):
        e = Env(self)
        e.arm("paper")
        res = e.buy()
        self.assertEqual((res.state, res.kind), (OrderState.FILLED, "SIMULATED"))
        ev = [r["event"] for r in e.sys.ledger.records()]
        self.assertEqual(ev[-2:], ["order_attempt", "order_result"])
        att = list(e.sys.ledger.records())[-2]
        self.assertTrue(all(att["checks"].values()))
        self.assertEqual((att["signal"], att["price"], att["size"]), ("test-signal", 1.10, 1))

    def test_live_requires_owner_reviewed(self):
        e = Env(self, dict(LIMITS, owner_reviewed=False))
        e.arm("live", e.clock() + timedelta(hours=1))
        with self.assertRaises(OrderBlocked):
            e.buy()

    def test_live_with_no_broker_blocked(self):
        e = Env(self)
        e.arm("live", e.clock() + timedelta(hours=1))
        with self.assertRaises(OrderBlocked):
            e.buy()

    def test_stale_or_unknown_data_blocks(self):
        for age in (6.0, None):
            e = Env(self, age=age)
            e.arm("paper")
            with self.assertRaises(OrderBlocked):
                e.buy()

    def test_size_cost_position_window(self):
        e = Env(self)
        e.arm("paper")
        for kwargs in ({"qty": 2}, {"price": 2.00}):
            with self.assertRaises(OrderBlocked):
                e.buy(**kwargs)
        e.clock.t = datetime(2026, 10, 30, 15, 45, tzinfo=NY)
        with self.assertRaises(OrderBlocked):
            e.buy()
        e.clock.t = datetime(2026, 10, 30, 10, 0, tzinfo=NY)
        e.buy()
        with self.assertRaises(OrderBlocked):  # position limit
            e.buy()

    def test_orders_per_day_and_reset_next_day(self):
        e = Env(self)
        e.arm("paper")
        e.buy(); e.sell(); e.buy(); e.sell()
        with self.assertRaises(OrderBlocked):
            e.buy()
        e.clock.t += timedelta(days=1)
        e.buy()

    def test_sell_allowed_outside_window_but_needs_position(self):
        e = Env(self)
        e.arm("paper")
        with self.assertRaises(OrderBlocked):
            e.sell()
        e.buy()
        e.clock.t = datetime(2026, 10, 30, 15, 50, tzinfo=NY)
        self.assertEqual(e.sell().state, OrderState.FILLED)

    def test_daily_loss_halts(self):
        e = Env(self)
        e.arm("paper")
        e.sys.gate.record_realized(-100.0)
        self.assertEqual(e.sys.switch.mode(), "off")
        e.arm("paper")
        with self.assertRaises(OrderBlocked):
            e.buy()
        self.assertEqual(e.sys.switch.mode(), "off")  # auto-halt again

    def test_missing_limits_blocks(self):
        e = Env(self)
        e.arm("paper")
        (e.tmp / "config" / "limits.json").write_text("{}")
        with self.assertRaises(OrderBlocked):
            e.buy()

    def test_halt_turns_off_and_cancels(self):
        e = Env(self)
        e.arm("paper")
        e.sys.broker.paper.place_order(OrderRequest(C, OrderSide.BUY, 1, 0.50))  # resting
        ids = e.sys.halt("test")
        self.assertEqual((e.sys.switch.mode(), len(ids)), ("off", 1))
        self.assertIsNone(e.sys.ledger.verify())


class LedgerTests(unittest.TestCase):
    def test_tamper_detected(self):
        e = Env(self)
        e.arm("paper"); e.buy()
        self.assertIsNone(e.sys.ledger.verify())
        p = e.sys.ledger.path
        lines = p.read_text().splitlines()
        rec = json.loads(lines[1]); rec["price"] = 9.99
        lines[1] = json.dumps(rec, sort_keys=True)
        p.write_text("\n".join(lines) + "\n")
        self.assertIn("edited", e.sys.ledger.verify())

    def test_deleted_line_detected(self):
        e = Env(self)
        e.arm("paper"); e.buy()
        p = e.sys.ledger.path
        lines = p.read_text().splitlines()
        p.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
        self.assertIsNotNone(e.sys.ledger.verify())


class LimitsTests(unittest.TestCase):
    def test_shipped_defaults_load_and_are_unreviewed(self):
        lim = Limits.load(Path(__file__).resolve().parent.parent / "config" / "limits.json")
        self.assertFalse(lim.owner_reviewed)

    def test_bad_values(self):
        tmp = Path(tempfile.mkdtemp()) / "l.json"
        tmp.write_text(json.dumps(dict(LIMITS, max_orders_per_day=0)))
        with self.assertRaises(LimitsError):
            Limits.load(tmp)


if __name__ == "__main__":
    unittest.main()
