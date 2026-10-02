import json
import tempfile
import unittest
from datetime import time, timedelta
from pathlib import Path

from data.sample import SAMPLE_CONTRACTS, SAMPLE_NOW, SAMPLE_SOURCE, sample_quote
from journal.records import ALERT_HEADER, Alert, DecisionRecord, Journal, TradeRecord, make_alert
from paper.sim import FeeModel, SlippageModel, simulate_buy_fill
from risk.checks import RiskResult
from signals.rules import Action, ExitParams, NoEntryRule, ProfitTargetExit

C = SAMPLE_CONTRACTS[0]
FEES = FeeModel(per_contract=0.65)
SLIP = SlippageModel(ticks=1, tick_size=0.01)
PARAMS = ExitParams(profit_target=0.15, stop_loss_fraction=0.20, latest_exit_time=time(15, 30))


class EntryRuleTests(unittest.TestCase):
    def test_default_rule_never_enters(self):
        s = NoEntryRule().evaluate(sample_quote(), SAMPLE_NOW)
        self.assertEqual(s.action, Action.NONE)
        self.assertEqual(s.quantity, 0)


class ExitRuleTests(unittest.TestCase):
    def setUp(self):
        self.entry = simulate_buy_fill(C, 1, sample_quote(bid=1.20, ask=1.26), FEES, SLIP, SAMPLE_NOW)
        self.rule = ProfitTargetExit(PARAMS, FEES, SLIP)

    def test_holds_below_target(self):
        s = self.rule.evaluate(self.entry, sample_quote(bid=1.40, ask=1.46), SAMPLE_NOW)
        self.assertEqual(s.action, Action.NONE)

    def test_exits_at_net_target_using_bid(self):
        s = self.rule.evaluate(self.entry, sample_quote(bid=1.50, ask=1.56), SAMPLE_NOW)
        self.assertEqual(s.action, Action.EXIT)
        self.assertIn("profit target", s.reason)

    def test_exits_on_soft_stop(self):
        # entry 1.27; sell at 0.99 -> net -29.30 on 127 = -23% <= -20%
        s = self.rule.evaluate(self.entry, sample_quote(bid=1.00, ask=1.06), SAMPLE_NOW)
        self.assertEqual(s.action, Action.EXIT)
        self.assertIn("soft stop", s.reason)

    def test_holds_above_soft_stop(self):
        # sell at 1.09 -> net -19.30 on 127 = -15.2% > -20%
        s = self.rule.evaluate(self.entry, sample_quote(bid=1.10, ask=1.16), SAMPLE_NOW)
        self.assertEqual(s.action, Action.NONE)

    def test_exits_at_latest_exit_time_even_without_quote(self):
        late = SAMPLE_NOW + timedelta(hours=5)
        s = self.rule.evaluate(self.entry, None, late)
        self.assertEqual(s.action, Action.EXIT)
        self.assertIn("latest exit time", s.reason)

    def test_no_quote_before_exit_time_holds(self):
        s = self.rule.evaluate(self.entry, None, SAMPLE_NOW)
        self.assertEqual(s.action, Action.NONE)


class JournalTests(unittest.TestCase):
    def test_alert_line_matches_header_shape(self):
        q = sample_quote(bid=1.20, ask=1.26, age_seconds=2.0)
        a = make_alert(SAMPLE_NOW, C.symbol(), "BLOCKED", "NO_ENTRY_RULE", q, RiskResult(False, "CONFIG", "x"))
        line = a.line()
        self.assertEqual(len(line.split(" | ")), len(ALERT_HEADER.split(" | ")))
        self.assertEqual(line, "14:30:00 | SPY 2026-10-02 500C | BLOCKED | NO_ENTRY_RULE | 2.0s | 0.06 | BLOCK:CONFIG")

    def test_alert_without_quote(self):
        a = make_alert(SAMPLE_NOW, C.symbol(), "BLOCKED", "NO_ENTRY_RULE", None, RiskResult(False, "NO_DATA", "x"))
        self.assertIn("| n/a | n/a | BLOCK:NO_DATA", a.line())

    def test_journal_writes_jsonl_and_labels_fills_simulated(self):
        entry = simulate_buy_fill(C, 1, sample_quote(), FEES, SLIP, SAMPLE_NOW)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "journal.jsonl"
            j = Journal(path)
            j.alert(make_alert(SAMPLE_NOW, C.symbol(), "ELIGIBLE", "r", sample_quote(), RiskResult(True, "OK", "ok")))
            j.decision(DecisionRecord(SAMPLE_NOW, C.symbol(), "ENTER_LONG", "r", ["all checks passed"], "OK",
                                      quote_source=SAMPLE_SOURCE, quote_observed_at=SAMPLE_NOW))
            j.trade(TradeRecord.from_fills(entry))
            lines = [json.loads(l) for l in path.read_text().splitlines()]
        self.assertEqual([l["kind"] for l in lines], ["alert", "decision", "trade"])
        self.assertEqual(lines[2]["fill_kind"], "SIMULATED")
        self.assertEqual(lines[2]["entry_price"], 1.27)
        self.assertEqual(lines[1]["quote_source"], "SAMPLE_SYNTHETIC")


if __name__ == "__main__":
    unittest.main()
