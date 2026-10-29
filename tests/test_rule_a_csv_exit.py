import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from data.csv_quotes import CSV_SOURCE, CsvQuoteSource, write_template
from data.sample import SAMPLE_CONTRACTS, SAMPLE_NOW, SampleQuoteSource, sample_quote
from journal.records import Journal
from paper.runner import (apply_entry, apply_exit, entry_rule_from_config, exit_rule_from_config,
                          run_exit_tick, run_tick)
from paper.sim import FeeModel, SlippageModel, simulate_buy_fill
from risk.checks import RiskConfig, SessionState
from signals.rules import Action, LiquidityWindowEntry, LiquidityWindowParams, NoEntryRule

ROOT = Path(__file__).resolve().parent.parent
C = SAMPLE_CONTRACTS[0]
FEES = FeeModel(per_contract=0.65)
SLIP = SlippageModel(1, 0.01)
PARAMS = LiquidityWindowParams.from_dict(dict(
    entry_start="09:45", entry_end="14:30", min_bid_size=5, min_ask_size=5, min_ask=1.00, max_ask=3.00))


class RuleATests(unittest.TestCase):
    def setUp(self):
        self.rule = LiquidityWindowEntry(PARAMS)

    def test_enters_inside_window_with_sizes_and_band(self):
        s = self.rule.evaluate(sample_quote(bid=1.20, ask=1.26), SAMPLE_NOW)  # 10:30 ET
        self.assertEqual(s.action, Action.ENTER_LONG)
        self.assertEqual(s.quantity, 1)

    def test_blocks_outside_window(self):
        early = SAMPLE_NOW - timedelta(hours=1)   # 09:30 ET
        late = SAMPLE_NOW + timedelta(hours=4)    # 14:30 ET, end is exclusive
        self.assertEqual(self.rule.evaluate(sample_quote(), early).action, Action.NONE)
        self.assertEqual(self.rule.evaluate(sample_quote(), late).action, Action.NONE)

    def test_blocks_small_size(self):
        self.assertEqual(self.rule.evaluate(sample_quote(bid_size=4), SAMPLE_NOW).action, Action.NONE)
        self.assertEqual(self.rule.evaluate(sample_quote(ask_size=4), SAMPLE_NOW).action, Action.NONE)

    def test_blocks_outside_premium_band(self):
        self.assertEqual(self.rule.evaluate(sample_quote(bid=0.90, ask=0.99), SAMPLE_NOW).action, Action.NONE)
        self.assertEqual(self.rule.evaluate(sample_quote(bid=3.00, ask=3.01), SAMPLE_NOW).action, Action.NONE)
        self.assertEqual(self.rule.evaluate(sample_quote(bid=2.95, ask=3.00), SAMPLE_NOW).action, Action.ENTER_LONG)

    def test_blocks_one_sided(self):
        self.assertEqual(self.rule.evaluate(sample_quote(bid=None), SAMPLE_NOW).action, Action.NONE)

    def test_built_from_paper_config(self):
        config = RiskConfig.from_file(ROOT / "config" / "paper.json")
        self.assertEqual(config.missing_required(), [])
        self.assertIsInstance(entry_rule_from_config(config), LiquidityWindowEntry)
        self.assertEqual(config.max_entries_per_session, 2)

    def test_bad_rule_params_fall_back_to_no_entry(self):
        config = RiskConfig.from_dict({"entry_rules": "LIQUIDITY_WINDOW_A", "rule_params": {"entry_start": "09:45"}})
        self.assertIsInstance(entry_rule_from_config(config), NoEntryRule)


class CsvSourceTests(unittest.TestCase):
    def test_latest_row_per_contract_and_labels(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "q.csv"
            write_template(path)
            with open(path, "a") as f:
                f.write("10:30:00,2026-10-02,500,CALL,1.20,1.26,50,40,500.10\n")
                f.write("10:31:00,2026-10-02,500,CALL,1.22,1.28,30,20,500.30\n")
                f.write("10:30:30,2026-10-02,500,PUT,,1.10,0,10,500.10\n")
                f.write("bad row\n")
            src = CsvQuoteSource(path)
            call = src.latest(SAMPLE_CONTRACTS[0])
            put = src.latest(SAMPLE_CONTRACTS[1])
        self.assertEqual(call.ask, 1.28)
        self.assertEqual(call.source, CSV_SOURCE)
        self.assertEqual(call.observed_at.isoformat(), "2026-10-02T10:31:00-04:00")
        self.assertEqual(call.age_seconds(SAMPLE_NOW + timedelta(seconds=75)), 15.0)
        self.assertIsNone(put.bid)
        self.assertFalse(put.is_two_sided)

    def test_missing_file_yields_no_quote(self):
        self.assertIsNone(CsvQuoteSource("/nonexistent/q.csv").latest(C))


class ExitTickAndStateTests(unittest.TestCase):
    def setUp(self):
        self.config = RiskConfig.from_dict(dict(
            entry_rules="LIQUIDITY_WINDOW_A", rule_params=dict(
                entry_start="09:45", entry_end="14:30", min_bid_size=5, min_ask_size=5, min_ask=1.0, max_ask=3.0),
            max_loss_per_trade=300.0, daily_loss_limit=300.0, quote_max_age_seconds=90, max_spread=0.05,
            latest_exit_time="15:30", stop_loss_fraction=0.5, max_entries_per_session=2,
            fees={"per_contract": 0.65}, slippage_ticks=1, tick_size=0.01))
        self.exit_rule = exit_rule_from_config(self.config, FEES, SLIP)
        self.entry = simulate_buy_fill(C, 1, sample_quote(bid=1.20, ask=1.25), FEES, SLIP, SAMPLE_NOW)

    def test_hold_then_exit_on_target_journals_trade(self):
        journal = Journal()
        hold = run_exit_tick(self.entry, SampleQuoteSource({C: sample_quote(bid=1.30, ask=1.35)}),
                             SAMPLE_NOW, journal, self.exit_rule, FEES, SLIP)
        self.assertFalse(hold.closed)
        self.assertEqual(hold.alert.status, "HOLD")
        later = SAMPLE_NOW + timedelta(minutes=5)
        out = run_exit_tick(self.entry, SampleQuoteSource({C: sample_quote(bid=1.50, ask=1.55, now=later)}),
                            later, journal, self.exit_rule, FEES, SLIP)
        self.assertTrue(out.closed)
        self.assertEqual(out.alert.status, "EXIT")
        self.assertEqual(out.exit_fill.price, 1.49)
        self.assertEqual(out.result.net_pnl, 21.70)  # 149 - 126 - 1.30
        self.assertEqual([e["kind"] for e in journal.entries], ["alert", "decision", "alert", "decision", "trade"])
        self.assertEqual(journal.entries[-1]["fill_kind"], "SIMULATED")
        state = apply_exit(apply_entry(SessionState(), self.entry), out.result)
        self.assertEqual(state.open_positions, 0)
        self.assertEqual(state.entries_this_session, 1)
        self.assertEqual(state.realized_pnl, 21.70)

    def test_exit_time_without_quote_stays_pending(self):
        late = SAMPLE_NOW + timedelta(hours=5)
        out = run_exit_tick(self.entry, SampleQuoteSource({}), late, Journal(), self.exit_rule, FEES, SLIP)
        self.assertFalse(out.closed)
        self.assertEqual(out.alert.status, "EXIT_PENDING")
        self.assertEqual(out.signal.action, Action.EXIT)

    def test_entries_per_session_cap_blocks(self):
        state = SessionState(entries_this_session=2)
        result = run_tick(self.config, [C], SampleQuoteSource({C: sample_quote(bid=1.20, ask=1.25)}),
                          SAMPLE_NOW, state, Journal())
        self.assertEqual(result.fills, [])
        self.assertEqual(result.alerts[0].risk_check, "BLOCK:ENTRIES")

    def test_full_config_enters_with_rule_a(self):
        result = run_tick(self.config, [C], SampleQuoteSource({C: sample_quote(bid=1.20, ask=1.25)}),
                          SAMPLE_NOW, SessionState(), Journal())
        self.assertEqual(len(result.fills), 1)
        self.assertEqual(result.alerts[0].rule, "LIQUIDITY_WINDOW_A")


if __name__ == "__main__":
    unittest.main()
