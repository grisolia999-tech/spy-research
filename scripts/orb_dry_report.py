"""Dry ORB report on the UNDERLYING only. Reads local CSVs, makes no network call, prices no options.

Per session: opening range, trigger, and what the SPY price did afterwards (best/worst excursion and
the 15:30 move in the signal's favour). Option P&L needs real option quotes and is NOT estimated here.
Usage: PYTHONPATH=src python3 scripts/orb_dry_report.py config/paper.json file.csv [file.csv ...]
"""
import json
import sys
from datetime import datetime, time
from pathlib import Path

from data.bars import bar_length, load_tradingview_csv
from data.quotes import Right
from signals.orb import OrbParams, orb_signal


def report(cfg_path, csv_paths):
    cfg = json.loads(Path(cfg_path).read_text())
    p = OrbParams.from_config(cfg["orb"])
    exit_t = time(int(cfg["orb"]["exit"]["latest_exit_time"][:2]), int(cfg["orb"]["exit"]["latest_exit_time"][3:5]))
    lines = ["| file | bar | session | range (lo-hi) | signal | trigger close | best move $ | worst move $ | 15:30 move $ |",
             "|---|---|---|---|---|---|---|---|---|"]
    n_sessions = n_signals = 0
    for path in csv_paths:
        bars = load_tradingview_csv(path)
        step = bar_length(bars)
        for day in sorted({b.start.date() for b in bars}):
            n_sessions += 1
            sig = orb_signal(bars, day, p)
            if sig is None:
                lines.append(f"| {Path(path).name} | {int(step.total_seconds()//60)}m | {day} | n/a | none (no trigger or incomplete opening range) | | | | |")
                continue
            n_signals += 1
            after = [b for b in bars if b.start.date() == day and b.start >= sig.trigger_close_time
                     and (b.start + step).time() <= exit_t]
            sign = 1 if sig.right is Right.CALL else -1
            fav = [(b.high - sig.trigger_price) * sign if sign == 1 else (sig.trigger_price - b.low) for b in after]
            adv = [(b.low - sig.trigger_price) if sign == 1 else (sig.trigger_price - b.high) for b in after]
            last = (after[-1].close - sig.trigger_price) * sign if after else float("nan")
            lines.append(f"| {Path(path).name} | {int(step.total_seconds()//60)}m | {day} | {sig.range_low:.2f}-{sig.range_high:.2f} "
                         f"| {sig.right.value} | {sig.trigger_close_time:%H:%M} @ {sig.trigger_price:.2f} "
                         f"| {max(fav):+.2f} | {min(adv):+.2f} | {last:+.2f} |" if after else
                         f"| {Path(path).name} | | {day} | | {sig.right.value} | | no bars after trigger | | |")
    return n_sessions, n_signals, "\n".join(lines)


if __name__ == "__main__":
    ns, nsig, table = report(sys.argv[1], sys.argv[2:])
    print(f"sessions examined: {ns}, signals: {nsig}\n\n{table}")
