"""Read-only download of SPY price history from the Schwab market-data API into data/history/.

Run on the Mac (it needs the Keychain login):  ./history

Safety: GET requests only. No orders, no switch, no account endpoints. Writes CSV files under
data/history/ (gitignored). Free; no billable calls.

UNVERIFIED (confirm from the first real response; the script prints what it got):
  - endpoint  GET https://api.schwabapi.com/marketdata/v1/pricehistory
  - params    symbol, periodType, period, frequencyType, frequency, startDate, endDate (epoch ms),
              needExtendedHoursData
  - response  {"candles":[{"open","high","low","close","volume","datetime"(epoch ms)}], "empty":bool}
  - depth     community reports: 1-minute about 48 days, 5-minute about 9 months, daily 20 years.
              If Schwab returns less, the script saves whatever came back and says so.
Schwab provides no historical option chains here; this is the underlying (SPY) only.
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://api.schwabapi.com/marketdata/v1/pricehistory"
OUT = Path("data/history")
DAY_MS = 86_400_000

# (label, frequencyType, frequency, chunk_days, total_days, periodType)
PLANS = [
    ("1min", "minute", 1, 10, 60, "day"),
    ("5min", "minute", 5, 30, 300, "day"),
    ("daily", "daily", 1, 3650, 7300, "year"),
]


def fetch(auth, params: dict) -> dict:
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {auth.access_token()}", "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code == 429 and attempt < 2:
                time.sleep(2 + attempt * 3)
                continue
            raise RuntimeError(f"HTTP {e.code}: {body}") from None
    raise RuntimeError("unreachable")


def pull(auth, symbol: str, label: str, ftype: str, freq: int, chunk_days: int,
         total_days: int, ptype: str) -> int:
    end = int(time.time() * 1000)
    start_floor = end - total_days * DAY_MS
    rows: dict = {}
    cur_end = end
    empty_streak = 0
    while cur_end > start_floor and empty_streak < 2:
        cur_start = max(cur_end - chunk_days * DAY_MS, start_floor)
        params = {"symbol": symbol, "periodType": ptype, "frequencyType": ftype,
                  "frequency": freq, "startDate": cur_start, "endDate": cur_end,
                  "needExtendedHoursData": "false"}
        try:
            body = fetch(auth, params)
        except RuntimeError as e:
            print(f"  {label}: stopped at {datetime.fromtimestamp(cur_end/1000, timezone.utc):%Y-%m-%d}: {e}")
            break
        candles = body.get("candles") or []
        if not candles:
            empty_streak += 1
        else:
            empty_streak = 0
            for c in candles:
                rows[c["datetime"]] = c
        cur_end = cur_start - 1
        time.sleep(0.6)  # market data limit reported as 120/min (UNVERIFIED)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{symbol}_{label}.csv"
    ordered = [rows[k] for k in sorted(rows)]
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch_ms", "utc", "open", "high", "low", "close", "volume"])
        for c in ordered:
            t = datetime.fromtimestamp(c["datetime"] / 1000, timezone.utc)
            w.writerow([c["datetime"], t.strftime("%Y-%m-%d %H:%M:%S"), c["open"], c["high"],
                        c["low"], c["close"], c["volume"]])
    if ordered:
        a = datetime.fromtimestamp(ordered[0]["datetime"] / 1000, timezone.utc)
        b = datetime.fromtimestamp(ordered[-1]["datetime"] / 1000, timezone.utc)
        print(f"  {label}: {len(ordered)} bars, {a:%Y-%m-%d} to {b:%Y-%m-%d} -> {path}")
    else:
        print(f"  {label}: no bars returned")
    return len(ordered)


def main() -> int:
    from schwabapi.auth import SchwabAuth
    auth = SchwabAuth.from_vault()
    symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    print(f"Pulling {symbol} history (read-only) ...")
    total = 0
    for plan in PLANS:
        total += pull(auth, symbol, *plan)
    print("DONE" if total else "NOTHING DOWNLOADED (see messages above)")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
