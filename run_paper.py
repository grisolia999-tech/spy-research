#!/usr/bin/env python3
"""Paper session loop. SIMULATED output only. Never establishes live profitability.

Usage:
  python run_paper.py --config config/paper.json --quotes quotes.csv [--interval 5] [--once]
  python run_paper.py --config config/paper.json --source schwab --login     # once a week
  python run_paper.py --config config/paper.json --source schwab --probe     # print one raw response
  python run_paper.py --config config/paper.json --source schwab [--interval 3]

Schwab mode reads SCHWAB_APP_KEY, SCHWAB_APP_SECRET, SCHWAB_CALLBACK_URL from the environment,
records every raw response to data/quotes-YYYY-MM-DD.jsonl, and only ever reads market data.

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
from data.schwab_quotes import SchwabAuth, SchwabLoginRequired, SchwabQuoteSource  # noqa: E402
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
    ap.add_argument("--source", choices=["csv", "schwab"], default="csv")
    ap.add_argument("--login", action="store_true", help="schwab: run the browser login and save tokens, then exit")
    ap.add_argument("--probe", action="store_true", help="schwab: fetch one raw quotes response, print it, exit")
    ap.add_argument("--record", default=None, help="schwab: raw response file (default data/quotes-<date>.jsonl)")
    args = ap.parse_args()

    if args.source == "schwab" and args.login:
        return schwab_login()

    config = RiskConfig.from_file(args.config)
    missing = config.missing_required()
    if missing:
        print("BLOCKED: required parameters unset in config:", ", ".join(missing))
        return 2
    contracts = [c for c in config.contracts() if c.strike > 0]
    if not contracts:
        print("BLOCKED: no contracts with a strike set in config 'contracts'")
        return 2

    session_date = datetime.now(ZoneInfo(config.session_timezone)).date().isoformat()
    if args.source == "schwab":
        try:
            auth = SchwabAuth.from_env()
        except Exception as e:  # missing env vars
            print(f"BLOCKED: {e}")
            return 2
        record = Path(args.record or f"data/quotes-{session_date}.jsonl")
        record.parent.mkdir(parents=True, exist_ok=True)
        source = SchwabQuoteSource(auth, contracts, underlying=config.raw.get("underlying", "SPY"), record_path=record)
        if args.probe:
            return schwab_probe(source)
        if config.quote_max_age_seconds and config.quote_max_age_seconds > 10:
            print(f"WARNING: quote_max_age_seconds={config.quote_max_age_seconds} is a manual-entry setting; "
                  f"use about 5 for an API feed.")
        quotes_path = None
    else:
        quotes_path = Path(args.quotes)
        if not quotes_path.exists():
            write_template(quotes_path)
            print(f"Created empty quote file {quotes_path}. Type quotes into it during the session.")
        source = CsvQuoteSource(quotes_path, config.session_timezone)

    journal_path = args.journal or f"journal-{session_date}.jsonl"
    journal = Journal(journal_path)
    fees, slippage = fee_model_from_config(config), slippage_model_from_config(config)
    entry_rule = entry_rule_from_config(config)
    exit_rule = exit_rule_from_config(config, fees, slippage)
    state = SessionState()
    position = None
    clock_tz = ZoneInfo(config.session_timezone)

    print(RESULT_LABEL)
    print(f"config={args.config} source={source.name} quotes={quotes_path} journal={journal_path} rule={entry_rule.rule_id}")
    print("contracts:", ", ".join(c.symbol() for c in contracts))
    print(ALERT_HEADER)

    try:
        while True:
            now = datetime.now(clock_tz)  # session-local, tz-aware; alerts print in this zone
            if hasattr(source, "refresh"):
                try:
                    source.refresh()
                except SchwabLoginRequired as e:
                    print(f"STOPPED: weekly Schwab login required ({e}). Run: python run_paper.py --source schwab --login")
                    break
                except Exception as e:  # network or API error: this tick has no data; risk blocks it
                    print(f"{now:%H:%M:%S} | quote fetch failed: {e}")
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


def schwab_login() -> int:
    try:
        auth = SchwabAuth.from_env()
    except Exception as e:
        print(f"BLOCKED: {e}")
        return 2
    print("1. Open this URL in a browser, log in to Schwab, and approve the app:")
    print("   " + auth.authorize_url())
    print("2. The browser will land on your callback URL and look like a dead page. That is expected.")
    print("3. Copy the FULL address from the browser bar and paste it here.")
    pasted = input("Pasted URL: ")
    try:
        auth.exchange_redirect(pasted)
    except Exception as e:
        print(f"LOGIN FAILED: {e}")
        return 1
    print(f"Tokens saved to {auth.token_path}. Access refreshes automatically; log in again in 7 days.")
    return 0


def schwab_probe(source: SchwabQuoteSource) -> int:
    """Fetch one response and print it so the field mapping can be checked against reality."""
    import json as _json
    try:
        snap = source.refresh()
    except SchwabLoginRequired as e:
        print(f"LOGIN REQUIRED: {e}")
        return 1
    print("symbols requested:", ", ".join(source.symbols()))
    print(_json.dumps(source.last_raw, indent=1)[:6000])
    print("--- parsed:")
    for c, q in snap.items():
        print(f"{c.symbol()}: bid={q.bid} ask={q.ask} sizes={q.bid_size}x{q.ask_size} observed={q.observed_at.isoformat()} source={q.source}")
    print(f"underlying last={source.underlying_last} observed={source.underlying_observed_at}")
    print("Nothing was traded. Raw response appended to", source.record_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
