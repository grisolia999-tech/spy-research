"""Risk limits loaded from config/limits.json. Any missing/invalid value blocks all orders."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import time
from pathlib import Path

REQUIRED = ("owner_reviewed", "max_contracts_per_order", "max_position_contracts", "max_order_cost_usd",
            "max_orders_per_day", "max_daily_loss_usd", "trading_window", "max_data_age_seconds")


class LimitsError(Exception):
    pass


@dataclass(frozen=True)
class Limits:
    owner_reviewed: bool
    max_contracts_per_order: int
    max_position_contracts: int
    max_order_cost_usd: float
    max_orders_per_day: int
    max_daily_loss_usd: float
    window_start: time
    window_end: time
    timezone: str
    max_data_age_seconds: float

    @classmethod
    def load(cls, path) -> "Limits":
        try:
            d = json.loads(Path(path).read_text())
            missing = [k for k in REQUIRED if k not in d]
            if missing:
                raise LimitsError("missing: " + ", ".join(missing))
            w = d["trading_window"]
            lim = cls(bool(d["owner_reviewed"]), int(d["max_contracts_per_order"]),
                      int(d["max_position_contracts"]), float(d["max_order_cost_usd"]),
                      int(d["max_orders_per_day"]), float(d["max_daily_loss_usd"]),
                      time.fromisoformat(w["start"]), time.fromisoformat(w["end"]), w["timezone"],
                      float(d["max_data_age_seconds"]))
        except LimitsError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise LimitsError(f"limits file unreadable or invalid: {e}") from e
        if min(lim.max_contracts_per_order, lim.max_position_contracts, lim.max_orders_per_day) <= 0 \
                or min(lim.max_order_cost_usd, lim.max_daily_loss_usd, lim.max_data_age_seconds) <= 0 \
                or lim.window_start >= lim.window_end:
            raise LimitsError("limits must be positive and window start must be before end")
        return lim
