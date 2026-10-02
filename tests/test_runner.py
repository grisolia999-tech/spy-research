import unittest
from pathlib import Path

from data.quotes import OptionQuote
from data.sample import SAMPLE_CONTRACTS, SAMPLE_NOW, SampleQuoteSource, sample_quote
from journal.records import Journal
from paper.runner import RESULT_LABEL, entry_rule_from_config, run_tick
from risk.checks import RiskConfig, SessionState
from signals.rules import Action, NoEntryRule, Signal

ROOT = Path(__file__).resolve().parent.parent

FULL = dict(
    entry_rules="STUB_ALWAYS_ENTER",
    max_loss_per_trade=200.0,
    daily_loss_limit=400.0,
    quote_max_age_seconds=5.0,
    max_spread=0.10,
    latest_exit_time="15:30",
    fees={"per_contract": 0.65, "per_order": 0.0},
    slippage_ticks=1,
    tick_size=0.01,
)


class StubEnterRule:
    """Test-only rule. Not a strategy."""

    rule_id = "STUB_ALWAYS_ENTER"

    def evaluate(self, quote: OptionQuote, now) -> Signal:
        return Signal(Action.ENTER_LONG, self.rule_id, "stub entry", 1)


class DefaultConfigTests(unittest.TestCase):
    def test_example_config_blocks_and_creates_nothing(self):
        config = RiskConfig.from_file(ROOT / "config" / "paper.example.json")
        journal = Journal()
        result = run_tick(config, SAMPLE_CONTRACTS, SampleQuoteSource(), SAMPLE_NOW, SessionState(), journal)

        self.assertEqual(result.orders, [])
        self.assertEqual(result.fills, [])
        self.assertEqual(result.label, RESULT_LABEL)
        self.assertEqual(len(result.alerts), len(SAMPLE_CONTRACTS))
        for alert in result.alerts:
            self.assertEqual(alert.status, "BLOCKED")
            self.assertEqual(alert.rule, NoEntryRule.rule_id)
            self.assertEqual(alert.risk_check, "BLOCK:NO_SIGNAL")
        self.assertEqual(
            result.alert_lines()[0],
            "14:30:00 | SPY 2026-10-02 500C | BLOCKED | NO_ENTRY_RULE | 1.0s | 0.06 | BLOCK:NO_SIGNAL",
        )
        kinds = [e["kind"] for e in journal.entries]
        self.assertEqual(kinds, ["alert", "decision"] * len(SAMPLE_CONTRACTS))
        self.assertEqual(journal.entries[1]["action"], "NONE")
        self.assertIn("entry rules not configured", journal.entries[1]["reasons"])

    def test_unknown_or_unset_entry_rule_falls_back_to_no_entry(self):
        self.assertIsInstance(entry_rule_from_config(RiskConfig.from_dict({})), NoEntryRule)
        self.assertIsInstance(entry_rule_from_config(RiskConfig.from_dict({"entry_rules": "nope"})), NoEntryRule)
        self.assertIsInstance(entry_rule_from_config(RiskConfig.from_dict({"entry_rules": "NO_ENTRY_RULE"})), NoEntryRule)


class StubEntryTests(unittest.TestCase):
    def test_enter_signal_with_full_config_yields_one_simulated_fill(self):
        config = RiskConfig.from_dict(FULL)
        contract = SAMPLE_CONTRACTS[0]
        source = SampleQuoteSource({contract: sample_quote(contract, bid=1.20, ask=1.26)})
        journal = Journal()
        result = run_tick(config, [contract], source, SAMPLE_NOW, SessionState(), journal, entry_rule=StubEnterRule())

        self.assertEqual(len(result.orders), 1)
        self.assertEqual(len(result.fills), 1)
        fill = result.fills[0]
        self.assertEqual(fill.kind, "SIMULATED")
        self.assertEqual(fill.price, 1.27)   # ask + 1 tick, never midpoint
        self.assertEqual(fill.fees, 0.65)
        self.assertEqual(result.alerts[0].status, "ELIGIBLE")
        self.assertEqual(result.alerts[0].risk_check, "PASS")
        self.assertEqual(journal.entries[1]["risk_code"], "OK")
        self.assertTrue(any("simulated buy 1 @ 1.27" in r for r in journal.entries[1]["reasons"]))

    def test_enter_signal_still_blocked_by_risk(self):
        config = RiskConfig.from_dict(FULL)
        contract = SAMPLE_CONTRACTS[0]
        source = SampleQuoteSource({contract: sample_quote(contract, age_seconds=30.0)})
        result = run_tick(config, [contract], source, SAMPLE_NOW, SessionState(), Journal(), entry_rule=StubEnterRule())
        self.assertEqual(result.fills, [])
        self.assertEqual(result.orders, [])
        self.assertEqual(result.alerts[0].status, "BLOCKED")
        self.assertEqual(result.alerts[0].risk_check, "BLOCK:STALE")

    def test_enter_signal_with_example_config_is_blocked_by_config_gate(self):
        config = RiskConfig.from_file(ROOT / "config" / "paper.example.json")
        result = run_tick(config, SAMPLE_CONTRACTS[:1], SampleQuoteSource(), SAMPLE_NOW, SessionState(), Journal(),
                          entry_rule=StubEnterRule())
        self.assertEqual(result.fills, [])
        self.assertEqual(result.alerts[0].risk_check, "BLOCK:CONFIG")

    def test_missing_quote_is_blocked_no_data(self):
        config = RiskConfig.from_dict(FULL)
        result = run_tick(config, SAMPLE_CONTRACTS[:1], SampleQuoteSource({}), SAMPLE_NOW, SessionState(), Journal(),
                          entry_rule=StubEnterRule())
        self.assertEqual(result.alerts[0].risk_check, "BLOCK:NO_DATA")
        self.assertIn("| n/a | n/a |", result.alert_lines()[0])


if __name__ == "__main__":
    unittest.main()
