import json
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from data.bars import Bar
from data.quotes import Right
from signals.orb import OrbParams, orb_signal

NY = ZoneInfo("America/New_York")
DAY = date(2026, 10, 30)
P = OrbParams.from_config(json.loads((Path(__file__).resolve().parent.parent / "config" / "paper.json").read_text())["orb"])


def series(closes_after, rng=(100.0, 101.0)):
    """1-minute bars: five opening-range bars 09:30-09:34 spanning rng, then the given closes from 09:35."""
    t0 = datetime(2026, 10, 30, 9, 30, tzinfo=NY)
    bars = [Bar(t0 + timedelta(minutes=i), 100.5, rng[1], rng[0], 100.5) for i in range(5)]
    for i, c in enumerate(closes_after):
        bars.append(Bar(t0 + timedelta(minutes=5 + i), c, c, c, c))
    return bars


class OrbTests(unittest.TestCase):
    def test_call_on_close_above_high(self):
        s = orb_signal(series([100.8, 101.2]), DAY, P)
        self.assertEqual((s.right, s.trigger_price, s.trigger_close_time.strftime("%H:%M")), (Right.CALL, 101.2, "09:37"))

    def test_put_on_close_below_low(self):
        self.assertEqual(orb_signal(series([100.5, 99.9]), DAY, P).right, Right.PUT)

    def test_wick_through_range_without_close_is_not_a_trigger(self):
        bars = series([100.5])
        bars.append(Bar(bars[-1].start + timedelta(minutes=1), 100.5, 105.0, 100.4, 100.6))
        self.assertIsNone(orb_signal(bars, DAY, P))

    def test_first_trigger_wins(self):
        self.assertEqual(orb_signal(series([101.5, 99.0]), DAY, P).right, Right.CALL)

    def test_incomplete_opening_range_gives_no_signal(self):
        self.assertIsNone(orb_signal(series([102.0])[1:], DAY, P))

    def test_trigger_after_window_ignored(self):
        bars = series([100.5] * 200 + [105.0])  # breakout at ~12:55, after 11:00
        self.assertIsNone(orb_signal(bars, DAY, P))

    def test_no_lookahead_bar_closing_before_entry_start_ignored(self):
        self.assertIsNone(orb_signal(series([]), DAY, P))


if __name__ == "__main__":
    unittest.main()
