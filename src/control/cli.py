"""./arm and ./halt. These are the only writers of config/switch.json.

arm live   : needs a real terminal, passing tests, owner_reviewed=true in limits.json,
             a valid Schwab login, and you typing CONFIRM. Expires at 16:00 New York time.
arm paper  : needs a real terminal.
arm off    : always allowed.
halt       : switch off, then cancel every open order (paper and live).
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]


def _build():
    from control.system import build
    live = None
    try:
        from broker.schwab_orders import SchwabBroker
        from schwabapi.auth import SchwabAuth
        auth = SchwabAuth.from_vault()
        live = SchwabBroker(auth, lambda req: None)  # cancel/read only here; place_order is never called
    except Exception as e:  # no Keychain / no login: halt still switches off
        print(f"note: live broker unavailable ({e}); live orders cannot be cancelled from here", file=sys.stderr)
    return build(ROOT, live_broker=live)


def _tests_pass() -> bool:
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-t", ".", "-s", "tests"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1500:] + r.stderr[-1500:])
    return r.returncode == 0


def arm(mode: str) -> int:
    from control.limits import Limits, LimitsError
    s = _build()
    if mode == "off":
        s.switch.set("off", "owner", "arm off")
        print("switch: OFF")
        return 0
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("Refused: ./arm paper|live must be run by you in a terminal.")
        return 2
    if mode == "paper":
        s.switch.set("paper", "owner", "arm paper")
        print("switch: PAPER (orders are simulated and logged)")
        return 0
    try:
        lim = Limits.load(ROOT / "config" / "limits.json")
    except LimitsError as e:
        print(f"Refused: {e}")
        return 3
    if not lim.owner_reviewed:
        print("Refused: set \"owner_reviewed\": true in config/limits.json after you review every limit.")
        return 3
    print(f"Limits: {lim.max_contracts_per_order} contract(s)/order, position <= {lim.max_position_contracts}, "
          f"cost <= ${lim.max_order_cost_usd:g}, {lim.max_orders_per_day} orders/day, "
          f"daily loss <= ${lim.max_daily_loss_usd:g}, window {lim.window_start}-{lim.window_end} {lim.timezone}, "
          f"data age <= {lim.max_data_age_seconds:g}s")
    print("Running tests...")
    if not _tests_pass():
        print("Refused: tests failing.")
        return 4
    try:
        from schwabapi.auth import SchwabAuth
        auth = SchwabAuth.from_vault()
        left = auth.seconds_until_refresh_expiry()
        auth.access_token()
        if left is None or left <= 0:
            raise RuntimeError("refresh token expired")
    except Exception as e:
        print(f"Refused: Schwab login not valid ({e}). Run ./reauth.")
        return 5
    if input("REAL MONEY. Type CONFIRM to arm live until 16:00 New York today: ").strip() != "CONFIRM":
        print("Not armed.")
        return 1
    tz = ZoneInfo(lim.timezone)
    until = datetime.combine(datetime.now(tz).date(), time(16, 0), tz).astimezone(timezone.utc)
    if until <= datetime.now(timezone.utc):
        print("Refused: market day is over.")
        return 6
    s.switch.set("live", "owner", "arm live", valid_until=until)
    print("switch: LIVE until 16:00 New York time. ./halt stops everything.")
    return 0


def halt() -> int:
    s = _build()
    try:
        ids = s.halt("./halt")
        print(f"switch: OFF. Cancelled {len(ids)} open order(s).")
        return 0
    except Exception as e:
        print(f"switch: OFF, but cancelling orders FAILED: {e}\nCheck open orders in Schwab NOW.")
        return 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "halt":
        sys.exit(halt())
    if cmd == "arm" and len(sys.argv) == 3 and sys.argv[2] in ("off", "paper", "live"):
        sys.exit(arm(sys.argv[2]))
    print("usage: arm off|paper|live   |   halt")
    sys.exit(64)
