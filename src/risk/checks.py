"""Trade eligibility checks.

Eligibility is blocked until all REQUIRED_PARAMETERS are set in the config, then until the
quote is present, fresh, two-sided and tight enough, position limits are respected, the
session loss limit is not breached, and the clock is before the latest exit time.
Checks are ordered; the first failure is the blocking reason.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from data.quotes import OptionQuote

REQUIRED_PARAMETERS: Sequence[str] = (
    "entry_rules",
    "max_loss_per_trade",
    "daily_loss_limit",
    "quote_max_age_seconds",
    "max_spread",
    "latest_exit_time",
)


@dataclass(frozen=True)
class RiskConfig:
    mode: str = "paper"
    profit_target: float = 0.15
    entry_rules: Optional[str] = None
    max_loss_per_trade: Optional[float] = None
    daily_loss_limit: Optional[float] = None
    quote_max_age_seconds: Optional[float] = None
    max_spread: Optional[float] = None               # absolute dollars per share
    latest_exit_time: Optional[str] = None           # "HH:MM" in session_timezone
    session_timezone: str = "America/New_York"
    max_open_positions: int = 1
    max_contracts_per_trade: int = 1
    raw: Dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RiskConfig":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "raw"}
        return cls(raw=d, **known)

    @classmethod
    def from_file(cls, path: str | Path) -> "RiskConfig":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def missing_required(self) -> List[str]:
        return [p for p in REQUIRED_PARAMETERS if getattr(self, p) in (None, "", [])]

    def latest_exit(self) -> Optional[time]:
        if not self.latest_exit_time:
            return None
        hh, mm = self.latest_exit_time.split(":")
        return time(int(hh), int(mm))


@dataclass(frozen=True)
class SessionState:
    realized_pnl: float = 0.0      # net of fees, negative when losing
    open_positions: int = 0


@dataclass(frozen=True)
class RiskResult:
    ok: bool
    code: str
    reason: str

    def label(self) -> str:
        return "PASS" if self.ok else f"BLOCK:{self.code}"


def check_eligibility(
    config: RiskConfig,
    quote: Optional[OptionQuote],
    now: datetime,
    state: SessionState,
    quantity: int,
) -> RiskResult:
    """Return the first blocking failure, or a PASS result."""
    if config.mode != "paper":
        return RiskResult(False, "MODE", f"only paper mode is supported, got {config.mode!r}")
    missing = config.missing_required()
    if missing:
        return RiskResult(False, "CONFIG", "required parameters unset: " + ", ".join(missing))

    tz = ZoneInfo(config.session_timezone)
    if now.astimezone(tz).time() >= config.latest_exit():
        return RiskResult(False, "TIME", f"at or after latest exit time {config.latest_exit_time}")

    if state.realized_pnl <= -config.daily_loss_limit:
        return RiskResult(False, "DAILY_LOSS", f"session loss {state.realized_pnl:.2f} hit limit {config.daily_loss_limit:.2f}")

    if quantity <= 0 or quantity > config.max_contracts_per_trade:
        return RiskResult(False, "SIZE", f"quantity {quantity} outside 1..{config.max_contracts_per_trade}")
    if state.open_positions >= config.max_open_positions:
        return RiskResult(False, "POSITIONS", f"open positions {state.open_positions} at limit {config.max_open_positions}")

    if quote is None:
        return RiskResult(False, "NO_DATA", "no quote available")
    if not quote.is_two_sided:
        return RiskResult(False, "NO_DATA", "quote is not two-sided")
    age = quote.age_seconds(now)
    if age < 0:
        return RiskResult(False, "CLOCK", f"quote observed {-age:.1f}s in the future; clock mismatch")
    if age > config.quote_max_age_seconds:
        return RiskResult(False, "STALE", f"quote age {age:.1f}s exceeds {config.quote_max_age_seconds}s")
    if quote.spread > config.max_spread:
        return RiskResult(False, "SPREAD", f"spread {quote.spread:.2f} exceeds {config.max_spread:.2f}")
    if quote.ask_size < quantity:
        return RiskResult(False, "SIZE", f"ask size {quote.ask_size} below quantity {quantity}")

    worst_case = quote.ask * 100 * quantity
    if worst_case > config.max_loss_per_trade:
        return RiskResult(False, "MAX_LOSS", f"premium at risk {worst_case:.2f} exceeds max loss {config.max_loss_per_trade:.2f}")

    return RiskResult(True, "OK", "all checks passed")
