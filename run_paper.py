#!/usr/bin/env python3
"""Paper session loop. SIMULATED output only. Never establishes live profitability.

Usage:
  python run_paper.py --config config/paper.json --quotes quotes.csv [--interval 5] [--once]

Each tick: for the open position (if any) evaluate the exit rule; otherwise evaluate entry
for each configured contract. Prints one alert line per evaluation and appends JSON lines to
the journal file. The clock is the machine clock, read once per tick and passed in.
"""
from __future__ import annotations

import argparse
import sys
import time as _time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from data.csv_quotes import CsvQuoteSource, write_template  # noqa: E402
from journal.records import ALERT_HEADER, Journal  # noqa: E402
from paper.runner import (RESULT_LABEL, apply_entry, apply_exit, entry_rule_from_config,  # noqa: E402
                          exit_rule_from_config, fee_model_from_config, run_exit_tick, run_tick,
                          slippage_model_from_config)
from risk.checks import RiskConfig, SessionState  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/paper.json")
    ap.add_argument("--quotes", default="quotes.csv")
    ap.add_argument("--journal", default=None, help="default: journal-YYYY-MM-DD.jsonl")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between ticks")
    ap.add_argument("--once", action="store_true", help="run a single tick and exit")
    args = ap.parse_args()

    config = RiskConfig.from_file(args.config)
    missing = config.missing_required()
    if missing:
        print("BLOCKED: required parameters unset in config:", ", ".join(missing))
        return 2
    contracts = [c for c in config.contracts() if c.strike > 0]
    if not contracts:
        print("BLOCKED: no contracts with a strike set in config 'contracts'")
        return 2

    quotes_path = Path(args.quotes)
    if not quotes_path.exists():
        write_template(quotes_path)
        print(f"Created empty quote file {quotes_path}. Type quotes into it during the session.")

    journal_path = args.journal or f"journal-{datetime.now(ZoneInfo(config.session_timezone)).date().isoformat()}.jsonl"
    journal = Journal(journal_path)
    source = CsvQuoteSource(quotes_path, config.session_timezone)
    fees, slippage = fee_model_from_config(config), slippage_model_from_config(config)
    entry_rule = entry_rule_from_config(config)
    exit_rule = exit_rule_from_config(config, fees, slippage)
    state = SessionState()
    position = None
    clock_tz = ZoneInfo(config.session_timezone)

    print(RESULT_LABEL)
    print(f"config={args.config} quotes={quotes_path} journal={journal_path} rule={entry_rule.rule_id}")
    print("contracts:", ", ".join(c.symbol() for c in contracts))
    print(ALERT_HEADER)

    try:
        while True:
            now = datetime.now(clock_tz)  # session-local, tz-aware; alerts print in this zone
            if position is not None:
                out = run_exit_tick(position, source, now, journal, exit_rule, fees, slippage)
                print(out.alert.line())
                if out.closed:
                    state = apply_exit(state, out.result)
                    position = None
                    print(f"  session realized net: {state.realized_pnl:.2f} (simulated)")
            else:
                result = run_tick(config, contracts, source, now, state, journal, entry_rule=entry_rule,
                                  fees=fees, slippage=slippage)
                for line in result.alert_lines():
                    print(line)
                if result.fills:
                    position = result.fills[0]
                    state = apply_entry(state, position)
                    print(f"  simulated entry: {position.contract.symbol()} {position.quantity} @ {position.price:.2f}")
            if args.once:
                break
            _time.sleep(args.interval)
    except KeyboardInterrupt:
        print("stopped")
    print(f"session realized net: {state.realized_pnl:.2f} (simulated; open position: {position is not None})")
    print(RESULT_LABEL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
