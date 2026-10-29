import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from control.system import build
from data.bars import Bar
from data.quotes import OptionQuote
from engine.bars import BarBuilder
from engine.engine import AgeBox, Engine
from engine.watch import TokenWatch
from tests.test_control import LIMITS

NY = ZoneInfo("America/New_York")
CFG = json.loads((Path(__file__).resolve().parent.parent / "config" / "paper.json").read_text())


class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


class FakeQuotes:
    """Underlying follows a script; any subscribed option quotes bid 1.00 / ask 1.05."""
    underlying = "SPY"

    def __init__(self, clock):
        self.clock, self.contracts, self.price = clock, [], 100.5
        self.underlying_last = None
        self.underlying_observed_at = None
        self.opt = {}
        self.bid, self.ask = 1.00, 1.05

    def refresh(self):
        self.underlying_last, self.underlying_observed_at = self.price, self.clock()
        self.opt = {c: OptionQuote(c, self.bid, self.ask, 10, 10, self.clock(), "FAKE") for c in self.contracts}
        return self.opt

    def latest(self, c):
        return self.opt.get(c)


class FakeAuth:
    left = 5 * 86400
    def seconds_until_refresh_expiry(self, now=None): return self.left


class Notes:
    def __init__(self): self.sent = []
    def send(self, t, m): self.sent.append((t, m))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "config").mkdir()
        (self.tmp / "config" / "limits.json").write_text(json.dumps(LIMITS))
        self.clock = Clock(datetime(2026, 10, 30, 9, 30, 5, tzinfo=NY).astimezone(timezone.utc))
        self.q = FakeQuotes(self.clock)
        box = AgeBox()
        self.sys = build(self.tmp, quote_for=lambda c: self.q.latest(c), data_age=box.get, clock=self.clock)
        self.auth, self.notes = FakeAuth(), Notes()
        self.watch = TokenWatch(self.auth, self.sys, self.notes, NY)
        self.eng = Engine(CFG, self.sys, self.q, BarBuilder(NY), self.watch, self.notes, box, self.clock)

    def run_minutes(self, start_min, prices):
        """Tick every 20s inside each minute; prices[i] is the price during minute start_min+i."""
        base = datetime(2026, 10, 30, 9, 30, tzinfo=NY)
        for i, p in enumerate(prices):
            for sec in (5, 25, 45):
                self.clock.t = (base + timedelta(minutes=start_min + i, seconds=sec)).astimezone(timezone.utc)
                self.q.price = p
                self.eng.run_once()

    def events(self, name):
        return [r for r in self.sys.ledger.records() if r["event"] == name]

    OR = [100.0, 101.0, 100.5, 100.2, 100.8]  # five minutes; range 100.0-101.0

    def test_off_logs_signal_but_never_orders(self):
        self.run_minutes(0, self.OR + [101.6, 101.7, 101.7])
        self.assertEqual(len(self.events("signal")), 1)
        self.assertEqual(self.events("order_attempt") + self.events("order_blocked"), [])
        self.assertEqual(self.eng.state, "FLAT")

    def test_paper_round_trip_target_hit(self):
        self.sys.switch.set("paper", "owner")
        self.run_minutes(0, self.OR + [101.6, 101.7])
        self.assertEqual(self.eng.state, "LONG")
        self.q.bid, self.q.ask = 1.30, 1.35  # sells at 1.29 -> net well above +15%
        self.run_minutes(8, [101.8])
        self.assertEqual(self.eng.state, "DONE")
        rt = self.events("round_trip")[0]
        self.assertEqual(rt["note"], "SIMULATED")
        self.assertGreater(rt["net_pnl"], 0)
        self.assertIsNone(self.sys.ledger.verify())

    def test_soft_stop_exit(self):
        self.sys.switch.set("paper", "owner")
        self.run_minutes(0, self.OR + [101.6, 101.7])
        self.q.bid, self.q.ask = 0.40, 0.45
        self.run_minutes(8, [101.0])
        self.assertEqual(self.eng.state, "DONE")
        self.assertLess(self.events("round_trip")[0]["net_pnl"], 0)

    def test_armed_too_late_skips_stale_signal(self):
        self.run_minutes(0, self.OR + [101.6, 101.7, 101.7, 101.7])
        self.sys.switch.set("paper", "owner")
        self.run_minutes(9, [101.7, 101.7])
        self.assertEqual(len(self.events("signal_expired")), 1)
        self.assertEqual(self.eng.state, "DONE")

    def test_switch_off_while_long_warns_and_does_not_sell(self):
        self.sys.switch.set("paper", "owner")
        self.run_minutes(0, self.OR + [101.6, 101.7])
        self.sys.switch.set("off", "owner")
        self.q.bid, self.q.ask = 1.30, 1.35
        self.run_minutes(8, [101.8])
        self.assertEqual(self.eng.state, "LONG")
        self.assertEqual(len(self.events("position_open_while_off")), 1)

    def test_stale_data_blocks_entry(self):
        self.sys.switch.set("paper", "owner")
        orig = self.q.refresh
        def old():
            r = orig(); self.q.underlying_observed_at = self.clock() - timedelta(seconds=30); return r
        self.q.refresh = old
        self.run_minutes(0, self.OR + [101.6, 101.7])
        self.assertEqual(self.events("order_attempt"), [])

    def test_token_expiry_halts_and_notifies(self):
        self.sys.switch.set("paper", "owner")
        self.auth.left = -1
        self.run_minutes(0, [100.0])
        self.assertEqual(self.sys.switch.mode(), "off")
        self.assertTrue(any("expired" in m for _, m in self.notes.sent))
        self.assertEqual(len(self.events("token_expired")), 1)

    def test_token_warning_once_per_day(self):
        self.auth.left = 10 * 3600
        self.run_minutes(0, [100.0, 100.0])
        self.assertEqual(len(self.events("token_expiry_warning")), 1)


class BarBuilderTests(unittest.TestCase):
    def test_completed_excludes_current_minute(self):
        b = BarBuilder(NY)
        t = datetime(2026, 10, 30, 9, 30, 10, tzinfo=NY)
        b.add(t, 100); b.add(t + timedelta(seconds=20), 101)
        self.assertEqual(b.completed(t + timedelta(seconds=30)), [])
        done = b.completed(t + timedelta(seconds=55))
        self.assertEqual([(x.open, x.high, x.close) for x in done], [(100, 101, 101)])


if __name__ == "__main__":
    unittest.main()
