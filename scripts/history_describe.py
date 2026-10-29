"""Descriptive statistics on downloaded SPY history (data/history/*.csv). No trading rules, no option prices.

Usage: python3 scripts/history_describe.py [history_dir]
Reports sample sizes next to every figure. Small samples are labelled, not interpreted.
"""
from __future__ import annotations

import csv
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def load(path: Path):
    rows = []
    with path.open() as f:
        for r in csv.DictReader(f):
            t = datetime.fromtimestamp(int(r["epoch_ms"]) / 1000, timezone.utc).astimezone(ET)
            rows.append((t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                         float(r["volume"])))
    return rows


def sessions(rows):
    d = defaultdict(list)
    for r in rows:
        if time(9, 30) <= r[0].time() < time(16, 0):
            d[r[0].date()].append(r)
    return {k: v for k, v in sorted(d.items()) if len(v) >= 50}


def pct(x):
    return f"{x*100:+.2f}%"


def daily_section(rows):
    print(f"\n== DAILY ({len(rows)} bars, {rows[0][0]:%Y-%m-%d} to {rows[-1][0]:%Y-%m-%d}) ==")
    gaps, rngs, rets, dow = [], [], [], defaultdict(list)
    for i in range(1, len(rows)):
        pc = rows[i - 1][4]
        o, h, l, c = rows[i][1:5]
        gaps.append(o / pc - 1)
        rngs.append((h - l) / pc)
        rets.append(c / pc - 1)
        dow[rows[i][0].weekday()].append((h - l) / pc)
    print(f"mean daily range {st.mean(rngs)*100:.2f}% | median {st.median(rngs)*100:.2f}% | n={len(rngs)}")
    print(f"gap (open vs prev close): mean {pct(st.mean(gaps))}, |gap|>0.5% on {sum(abs(g)>0.005 for g in gaps)/len(gaps)*100:.1f}% of days")
    up = [g for g in gaps if g > 0.002]
    dn = [g for g in gaps if g < -0.002]
    cont_up = sum(1 for i in range(1, len(rows)) if gaps[i - 1] > 0.002 and rets[i - 1] > gaps[i - 1])
    cont_dn = sum(1 for i in range(1, len(rows)) if gaps[i - 1] < -0.002 and rets[i - 1] < gaps[i - 1])
    print(f"gaps >+0.2%: {len(up)} days, day closed above its gap-adjusted open {cont_up/len(up)*100:.0f}% of the time"
          if up else "")
    print(f"gaps <-0.2%: {len(dn)} days, day closed below it {cont_dn/len(dn)*100:.0f}% of the time" if dn else "")
    names = "Mon Tue Wed Thu Fri".split()
    print("mean range by weekday: " + ", ".join(f"{names[k]} {st.mean(v)*100:.2f}% (n={len(v)})" for k, v in sorted(dow.items()) if k < 5))
    ac = st.correlation(rets[:-1], rets[1:])
    print(f"lag-1 autocorrelation of daily returns: {ac:+.3f} (n={len(rets)-1})")
    big = sorted(rets)
    print(f"worst day {pct(big[0])}, best day {pct(big[-1])}; days |ret|>1%: {sum(abs(r)>0.01 for r in rets)/len(rets)*100:.1f}%")


def intraday_section(name, rows, bar_min):
    ss = sessions(rows)
    print(f"\n== {name} ({len(ss)} sessions, {min(ss)} to {max(ss)}) ==")
    # time-of-day movement
    buckets = defaultdict(list)
    for d, bars in ss.items():
        for b in bars:
            k = (b[0].hour * 60 + b[0].minute) // 30 * 30
            buckets[k].append(abs(b[4] / b[1] - 1) / bar_min ** 0.5)  # per-sqrt-minute so bar size doesn't matter
    print("avg |bar move| per sqrt-minute by 30-min block (relative activity):")
    base = st.mean(buckets[9 * 60 + 30])
    for k in sorted(buckets):
        print(f"  {k//60:02d}:{k%60:02d}  {st.mean(buckets[k])/base:5.2f}x of the open block  (n={len(buckets[k])})")
    # opening range behaviour (first 5 minutes)
    orw, ors, res = [], 0, []
    hi_first, lo_first, hi_last, lo_last = 0, 0, 0, 0
    for d, bars in ss.items():
        first5 = [b for b in bars if b[0].time() < time(9, 35)]
        if not first5:
            continue
        orh, orl = max(b[2] for b in first5), min(b[3] for b in first5)
        px = first5[0][1]
        orw.append((orh - orl) / px)
        day_close = bars[-1][4]
        after = [b for b in bars if b[0].time() >= time(9, 35)]
        brk = None
        for b in after:
            if b[0].time() > time(11, 0):
                break
            if b[4] > orh:
                brk = "up"
                break
            if b[4] < orl:
                brk = "down"
                break
        if brk:
            ents = b[4]
            res.append((brk, day_close / ents - 1, (max(x[2] for x in after if x[0] >= b[0]) / ents - 1) if brk == "up"
                        else (ents / min(x[3] for x in after if x[0] >= b[0]) - 1)))
        dh = max(b[2] for b in bars)
        dl = min(b[3] for b in bars)
        hi_first += max(bars, key=lambda b: b[2])[0].time() < time(10, 30)
        lo_first += min(bars, key=lambda b: b[3])[0].time() < time(10, 30)
        hi_last += max(bars, key=lambda b: b[2])[0].time() >= time(15, 0)
        lo_last += min(bars, key=lambda b: b[3])[0].time() >= time(15, 0)
    n = len(ss)
    print(f"5-min opening range width: mean {st.mean(orw)*100:.2f}%, median {st.median(orw)*100:.2f}% (n={len(orw)})")
    print(f"day high set before 10:30: {hi_first/n*100:.0f}%, after 15:00: {hi_last/n*100:.0f}% | day low before 10:30: {lo_first/n*100:.0f}%, after 15:00: {lo_last/n*100:.0f}% (n={n})")
    if res:
        for side in ("up", "down"):
            r = [x for x in res if x[0] == side]
            if r:
                same = [(x[1] > 0) if side == "up" else (x[1] < 0) for x in r]
                print(f"first close beyond range ({side}, 09:35-11:00): n={len(r)}, day closed in the break direction {sum(same)/len(r)*100:.0f}% of the time, "
                      f"median underlying move entry->close {pct(st.median([x[1] for x in r]))}, "
                      f"median max favourable excursion {pct(st.median([x[2] for x in r]))}")
        print(f"days with no break by 11:00: {n-len(res)} of {n}")
    # last 30 minutes vs rest of day
    cont = []
    for d, bars in ss.items():
        pre = [b for b in bars if b[0].time() < time(15, 30)]
        post = [b for b in bars if b[0].time() >= time(15, 30)]
        if pre and post:
            r1 = pre[-1][4] / bars[0][1] - 1
            r2 = post[-1][4] / pre[-1][4] - 1
            cont.append((r1, r2))
    same = sum(1 for a, b in cont if a * b > 0)
    print(f"last 30 min moves in the same direction as the day so far: {same/len(cont)*100:.0f}% (n={len(cont)}); median |last-30-min move| {st.median([abs(b) for a,b in cont])*100:.2f}%")


def main():
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "data/history")
    daily_section(load(d / "SPY_daily.csv"))
    intraday_section("5-MINUTE BARS", load(d / "SPY_5min.csv"), 5)
    intraday_section("1-MINUTE BARS", load(d / "SPY_1min.csv"), 1)
    print("\nDescriptive only: no option prices, no costs, no trading rules. Small samples are not evidence of a pattern.")


if __name__ == "__main__":
    main()
