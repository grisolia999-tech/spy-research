"""Alert, trade and decision records plus a minimal JSON-lines journal."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from data.quotes import OptionQuote
from paper.sim import PaperFill
from risk.checks import RiskResult

ALERT_HEADER = "TIME | CONTRACT | STATUS | RULE | DATA AGE | SPREAD | RISK CHECK"


@dataclass(frozen=True)
class Alert:
    time: datetime
    contract: str
    status: str        # e.g. BLOCKED, ELIGIBLE, ENTER, EXIT, HOLD
    rule: str
    data_age_seconds: Optional[float]
    spread: Optional[float]
    risk_check: str    # RiskResult.label()

    def line(self) -> str:
        age = "n/a" if self.data_age_seconds is None else f"{self.data_age_seconds:.1f}s"
        spread = "n/a" if self.spread is None else f"{self.spread:.2f}"
        return " | ".join([
            self.time.strftime("%H:%M:%S"), self.contract, self.status, self.rule, age, spread, self.risk_check,
        ])


def make_alert(now: datetime, contract: str, status: str, rule: str,
               quote: Optional[OptionQuote], risk: RiskResult) -> Alert:
    return Alert(
        time=now,
        contract=contract,
        status=status,
        rule=rule,
        data_age_seconds=None if quote is None else quote.age_seconds(now),
        spread=None if quote is None else quote.spread,
        risk_check=risk.label(),
    )


@dataclass(frozen=True)
class DecisionRecord:
    time: datetime
    contract: str
    action: str
    rule: str
    reasons: List[str]
    risk_code: str
    quote_source: Optional[str] = None
    quote_observed_at: Optional[datetime] = None


@dataclass(frozen=True)
class TradeRecord:
    """Round trip built from SIMULATED fills. Fields never contain observed-quote prices."""

    contract: str
    quantity: int
    entry_price: float
    entry_fees: float
    entry_at: datetime
    exit_price: Optional[float] = None
    exit_fees: Optional[float] = None
    exit_at: Optional[datetime] = None
    net_pnl: Optional[float] = None
    return_on_premium: Optional[float] = None
    exit_reason: Optional[str] = None
    fill_kind: str = "SIMULATED"

    @classmethod
    def from_fills(cls, entry: PaperFill, exit_fill: Optional[PaperFill] = None,
                   net_pnl: Optional[float] = None, return_on_premium: Optional[float] = None,
                   exit_reason: Optional[str] = None) -> "TradeRecord":
        return cls(
            contract=entry.contract.symbol(),
            quantity=entry.quantity,
            entry_price=entry.price,
            entry_fees=entry.fees,
            entry_at=entry.filled_at,
            exit_price=None if exit_fill is None else exit_fill.price,
            exit_fees=None if exit_fill is None else exit_fill.fees,
            exit_at=None if exit_fill is None else exit_fill.filled_at,
            net_pnl=net_pnl,
            return_on_premium=return_on_premium,
            exit_reason=exit_reason,
        )


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"not serializable: {type(o).__name__}")


class Journal:
    """Append-only journal. In memory by default; pass a path to also write JSON lines."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self.entries: List[Dict[str, Any]] = []

    def record(self, kind: str, item: Any) -> Dict[str, Any]:
        entry = {"kind": kind, **asdict(item)}
        self.entries.append(entry)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=_json_default) + "\n")
        return entry

    def alert(self, a: Alert) -> Dict[str, Any]:
        return self.record("alert", a)

    def decision(self, d: DecisionRecord) -> Dict[str, Any]:
        return self.record("decision", d)

    def trade(self, t: TradeRecord) -> Dict[str, Any]:
        return self.record("trade", t)
