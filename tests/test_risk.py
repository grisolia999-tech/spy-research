import json
import unittest
from datetime import timedelta
from pathlib import Path

from data.sample import SAMPLE_NOW, sample_quote
from risk.checks import REQUIRED_PARAMETERS, RiskConfig, SessionState, check_eligibility

ROOT = Path(__file__).resolve().parent.parent

FULL = dict(
    entry_rules="placeholder_rule",
    max_loss_per_trade=200.0,
    daily_loss_limit=400.0,
    quote_max_age_seconds=5.0,
    max_spread=0.10,
    latest_exit_time="15:30",
)


def cfg(**overrides):
    d = {**FULL, **overrides}
    return RiskConfig.from_dict(d)


class ConfigGatingTests(unittest.TestCase):
    def test_example_config_blocks_until_required_params_set(self):
        config = RiskConfig.from_file(ROOT / "config" / "paper.example.json")
        self.assertEqual(config.mode, "paper")
        self.assertEqual(config.profit_target, 0.15)
        self.assertEqual(config.missing_required(), list(REQUIRED_PARAMETERS))
        result = check_eligibility(config, sample_quote(), SAMPLE_NOW, SessionState(), 1)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "CONFIG")
        for p in REQUIRED_PARAMETERS:
            self.assertIn(p, result.reason)

    def test_each_required_param_blocks_alone(self):
        for p in REQUIRED_PARAMETERS:
            with self.subTest(param=p):
                result = check_eligibility(cfg(**{p: None}), sample_quote(), SAMPLE_NOW, SessionState(), 1)
                self.assertEqual(result.code, "CONFIG")
                self.assertIn(p, result.reason)

    def test_full_config_passes_with_fresh_tight_quote(self):
        result = check_eligibility(cfg(), sample_quote(), SAMPLE_NOW, SessionState(), 1)
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.label(), "PASS")

    def test_non_paper_mode_blocks(self):
        result = check_eligibility(cfg(mode="live"), sample_quote(), SAMPLE_NOW, SessionState(), 1)
        self.assertEqual(result.code, "MODE")


class DataChecksTests(unittest.TestCase):
    def test_missing_quote_blocks(self):
        result = check_eligibility(cfg(), None, SAMPLE_NOW, SessionState(), 1)
        self.assertEqual(result.code, "NO_DATA")

    def test_one_sided_quote_blocks(self):
        result = check_eligibility(cfg(), sample_quote(bid=None), SAMPLE_NOW, SessionState(), 1)
        self.assertEqual(result.code, "NO_DATA")

    def test_stale_quote_blocks(self):
        result = check_eligibility(cfg(), sample_quote(age_seconds=6.0), SAMPLE_NOW, SessionState(), 1)
        self.assertEqual(result.code, "STALE")

    def test_future_quote_blocks(self):
        result = check_eligibility(cfg(), sample_quote(age_seconds=-2.0), SAMPLE_NOW, SessionState(), 1)
        self.assertEqual(result.code, "CLOCK")

    def test_wide_spread_blocks(self):
        result = check_eligibility(cfg(), sample_quote(bid=1.00, ask=1.20), SAMPLE_NOW, SessionState(), 1)
        self.assertEqual(result.code, "SPREAD")

    def test_insufficient_ask_size_blocks(self):
        result = check_eligibility(cfg(max_contracts_per_trade=5), sample_quote(ask_size=2), SAMPLE_NOW, SessionState(), 3)
        self.assertEqual(result.code, "SIZE")


class LimitChecksTests(unittest.TestCase):
    def test_open_position_limit_blocks(self):
        result = check_eligibility(cfg(), sample_quote(), SAMPLE_NOW, SessionState(open_positions=1), 1)
        self.assertEqual(result.code, "POSITIONS")

    def test_quantity_above_max_blocks(self):
        result = check_eligibility(cfg(), sample_quote(), SAMPLE_NOW, SessionState(), 2)
        self.assertEqual(result.code, "SIZE")

    def test_daily_loss_limit_blocks(self):
        result = check_eligibility(cfg(), sample_quote(), SAMPLE_NOW, SessionState(realized_pnl=-400.0), 1)
        self.assertEqual(result.code, "DAILY_LOSS")

    def test_premium_above_max_loss_blocks(self):
        # ask 1.26 * 100 = 126 at risk; limit 100
        result = check_eligibility(cfg(max_loss_per_trade=100.0), sample_quote(), SAMPLE_NOW, SessionState(), 1)
        self.assertEqual(result.code, "MAX_LOSS")

    def test_after_latest_exit_time_blocks(self):
        late = SAMPLE_NOW + timedelta(hours=5)  # 15:30 ET
        result = check_eligibility(cfg(), sample_quote(now=late), late, SessionState(), 1)
        self.assertEqual(result.code, "TIME")


if __name__ == "__main__":
    unittest.main()
