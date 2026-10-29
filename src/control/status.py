"""./status: one screen of facts. Read-only."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from control.ledger import Ledger
from control.switch import Switch

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    led = Ledger(ROOT / "logs" / "trades.jsonl")
    print("switch:", Switch(ROOT / "config" / "switch.json").mode().upper())
    problem = led.verify()
    print("ledger:", "intact" if problem is None else "PROBLEM: " + problem)
    try:
        from schwabapi.auth import SchwabAuth
        left = SchwabAuth.from_vault().seconds_until_refresh_expiry()
        print("schwab login:", "none saved (run ./reauth)" if left is None else
              ("EXPIRED (run ./reauth)" if left <= 0 else f"{left/3600:.0f}h left"))
    except Exception as e:
        print("schwab login: unknown (%s)" % e)
    print("last events:")
    for r in list(led.records())[-8:]:
        print(" ", r["ts"][:19], r["event"], r.get("mode", ""), r.get("contract", ""), r.get("side", ""),
              r.get("price", ""), r.get("size", ""), r.get("state", ""), r.get("failed", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
