import unittest
from datetime import timedelta

from data.sample import SAMPLE_CONTRACTS, SAMPLE_NOW, sample_quote
from paper.sim import (FILL_KIND, FeeModel, OrderSide, PaperOrder, SlippageModel, round_trip_return,
                       simulate_buy_fill, simulate_fill, simulate_sell_fill)

C = SAMPLE_CONTRACTS[0]
FEES = FeeModel(per_contract=0.65, per_order=0.0)
SLIP = SlippageModel(ticks=1, tick_size=0.01)


class FillTests(unittest.TestCase):
    def test_buy_fills_at_ask_plus_slippage_not_mid(self):
        q = sample_quote(bid=1.20, ask=1.26)
        fill = simulate_buy_fill(C, 1, q, FEES, SLIP, SAMPLE_NOW)
        self.assertEqual(fill.price, 1.27)
        self.assertNotEqual(fill.price, q.mid)
        self.assertEqual(fill.kind, FILL_KIND)
        self.assertEqual(fill.quote_source, q.source)
        self.assertEqual(fill.fees, 0.65)

    def test_sell_fills_at_bid_minus_slippage(self):
        fill = simulate_sell_fill(C, 1, sample_quote(bid=1.20, ask=1.26), FEES, SLIP, SAMPLE_NOW)
        self.assertEqual(fill.price, 1.19)

    def test_sell_price_floors_at_tick(self):
        fill = simulate_sell_fill(C, 1, sample_quote(bid=0.01, ask=0.02), FEES, SLIP, SAMPLE_NOW)
        self.assertEqual(fill.price, 0.01)

    def test_quantity_capped_at_displayed_size(self):
        fill = simulate_buy_fill(C, 5, sample_quote(ask_size=2), FEES, SLIP, SAMPLE_NOW)
        self.assertEqual(fill.quantity, 2)
        self.assertEqual(fill.fees, 1.30)

    def test_no_size_or_one_sided_returns_none(self):
        self.assertIsNone(simulate_buy_fill(C, 1, sample_quote(ask_size=0), FEES, SLIP, SAMPLE_NOW))
        self.assertIsNone(simulate_buy_fill(C, 1, sample_quote(ask=None), FEES, SLIP, SAMPLE_NOW))
        self.assertIsNone(simulate_fill(PaperOrder(C, OrderSide.BUY, 0, SAMPLE_NOW), sample_quote(), FEES, SLIP, SAMPLE_NOW))


class ReturnTests(unittest.TestCase):
    def test_round_trip_includes_fees_and_slippage(self):
        entry = simulate_buy_fill(C, 1, sample_quote(bid=1.20, ask=1.26), FEES, SLIP, SAMPLE_NOW)
        later = SAMPLE_NOW + timedelta(minutes=10)
        exit_fill = simulate_sell_fill(C, 1, sample_quote(bid=1.50, ask=1.56, now=later), FEES, SLIP, later)
        r = round_trip_return(entry, exit_fill)
        self.assertEqual(r.entry_premium, 127.0)   # 1.27 * 100
        self.assertEqual(r.exit_proceeds, 149.0)   # 1.49 * 100
        self.assertEqual(r.total_fees, 1.30)
        self.assertEqual(r.net_pnl, 20.70)
        self.assertAlmostEqual(r.return_on_premium, 20.70 / 127.0, places=6)

    def test_midpoint_round_trip_would_overstate_return(self):
        # Same quotes as above: mid-to-mid would be (1.53-1.23)/1.23 = 24.4%, net executable is 16.3%.
        entry = simulate_buy_fill(C, 1, sample_quote(bid=1.20, ask=1.26), FEES, SLIP, SAMPLE_NOW)
        exit_fill = simulate_sell_fill(C, 1, sample_quote(bid=1.50, ask=1.56), FEES, SLIP, SAMPLE_NOW)
        r = round_trip_return(entry, exit_fill)
        self.assertLess(r.return_on_premium, (1.53 - 1.23) / 1.23)

    def test_fifteen_percent_target_needs_bid_above_breakeven(self):
        entry = simulate_buy_fill(C, 1, sample_quote(bid=1.20, ask=1.26), FEES, SLIP, SAMPLE_NOW)
        # Entry 127.00 + 1.30 round-trip fees. Net >= 0.15 * 127 = 19.05 needs proceeds >= 147.35,
        # i.e. sell price >= 1.4735, i.e. bid >= 1.4835 after 0.01 slippage. Bid 1.48 misses, 1.49 clears.
        below = simulate_sell_fill(C, 1, sample_quote(bid=1.48, ask=1.54), FEES, SLIP, SAMPLE_NOW)
        above = simulate_sell_fill(C, 1, sample_quote(bid=1.49, ask=1.55), FEES, SLIP, SAMPLE_NOW)
        self.assertLess(round_trip_return(entry, below).return_on_premium, 0.15)
        self.assertGreaterEqual(round_trip_return(entry, above).return_on_premium, 0.15)

    def test_loss_round_trip(self):
        entry = simulate_buy_fill(C, 1, sample_quote(bid=1.20, ask=1.26), FEES, SLIP, SAMPLE_NOW)
        exit_fill = simulate_sell_fill(C, 1, sample_quote(bid=1.00, ask=1.06), FEES, SLIP, SAMPLE_NOW)
        r = round_trip_return(entry, exit_fill)
        self.assertEqual(r.net_pnl, -29.30)
        self.assertLess(r.return_on_premium, 0)

    def test_mismatched_fills_rejected(self):
        entry = simulate_buy_fill(C, 1, sample_quote(), FEES, SLIP, SAMPLE_NOW)
        with self.assertRaises(ValueError):
            round_trip_return(entry, entry)
        other = simulate_sell_fill(SAMPLE_CONTRACTS[1], 1, sample_quote(SAMPLE_CONTRACTS[1]), FEES, SLIP, SAMPLE_NOW)
        with self.assertRaises(ValueError):
            round_trip_return(entry, other)


if __name__ == "__main__":
    unittest.main()
