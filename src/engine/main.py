"""Engine process (run by launchd). ALWAYS starts with the switch OFF.

    PYTHONPATH=src python3 -m engine.main [--interval 3]
Stops cleanly on SIGTERM/SIGINT: switch off and open orders cancelled.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time as _time
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from broker.interface import OrderBlocked
from broker.schwab_orders import SchwabBroker
from control.system import build
from data.schwab_quotes import SchwabQuoteSource
from engine.bars import BarBuilder
from engine.engine import AgeBox, Engine
from engine.notify import Notifier
from engine.watch import TokenWatch
from paper.sim import FeeModel, SlippageModel
from schwabapi.auth import SchwabAuth

ROOT = Path(__file__).resolve().parents[2]
OPEN, CLOSE = time(9, 25), time(16, 5)


def market_hours(now: datetime, tz: ZoneInfo) -> bool:
    n = now.astimezone(tz)
    return n.weekday() < 5 and OPEN <= n.time() <= CLOSE


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--config", default=str(ROOT / "config" / "paper.json"))
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text())
    tz = ZoneInfo(cfg.get("session_timezone", "America/New_York"))
    clock = lambda: datetime.now(timezone.utc)
    auth = SchwabAuth.from_vault()
    holder = {}

    def live_gate(req):  # second lock: the live broker re-checks the switch itself
        if holder["sys"].switch.mode() != "live":
            raise OrderBlocked("live broker refused: switch is not live")

    box = AgeBox()
    quotes = SchwabQuoteSource(auth, [], cfg.get("underlying", "SPY"),
                               record_path=ROOT / "data" / f"quotes-{clock():%Y-%m-%d}.jsonl", clock=clock)
    fees = FeeModel(float(cfg["fees"]["per_contract"]), float(cfg["fees"].get("per_order", 0.0)))
    slip = SlippageModel(int(cfg.get("slippage_ticks", 1)), float(cfg.get("tick_size", 0.01)))
    system = build(ROOT, live_broker=SchwabBroker(auth, live_gate), quote_for=quotes.latest,
                   data_age=box.get, fees=fees, slippage=slip)
    holder["sys"] = system
    system.switch.reset_to_off("engine start")
    notifier = Notifier(system.ledger)
    engine = Engine(cfg, system, quotes, BarBuilder(tz), TokenWatch(auth, system, notifier, tz), notifier, box, clock)
    system.ledger.append("engine_start", mode="off", checks={})
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *a: stop.update(now=True))
    while not stop["now"]:
        try:
            if market_hours(clock(), tz):
                engine.run_once()
                _time.sleep(args.interval)
            else:
                engine.watch.check(clock())  # keep warning about login expiry outside market hours
                _time.sleep(30)
        except Exception as e:  # never die silently; log, tell the owner, keep the loop alive
            system.ledger.append("engine_error", error=repr(e), checks={})
            notifier.send("Trading engine error", str(e)[:120])
            _time.sleep(10)
    try:
        system.halt("engine stopping")
    finally:
        system.ledger.append("engine_stop", checks={})
    return 0


if __name__ == "__main__":
    sys.exit(main())
