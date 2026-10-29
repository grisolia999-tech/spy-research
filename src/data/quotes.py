"""Timestamped option quote interface.

An OptionQuote is an OBSERVED quote. It is not a fill and must never be used as one.
Simulated fills live in src/paper and carry their own type.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional, Protocol


class Right(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    expiry: date
    strike: float
    right: Right

    def symbol(self) -> str:
        """Compact human-readable label, e.g. SPY 2026-10-02 500C."""
        return f"{self.underlying} {self.expiry.isoformat()} {self.strike:g}{self.right.value[0]}"


@dataclass(frozen=True)
class OptionQuote:
    """One observed NBBO-style quote.

    observed_at must be timezone-aware. source names where the quote came from
    (e.g. a provider id or SAMPLE_SYNTHETIC). bid/ask may be None when one side is missing.
    """

    contract: OptionContract
    bid: Optional[float]
    ask: Optional[float]
    bid_size: int
    ask_size: int
    observed_at: datetime
    source: str
    underlying_price: Optional[float] = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("crossed quote: ask < bid")

    @property
    def is_two_sided(self) -> bool:
        return self.bid is not None and self.ask is not None and self.bid > 0 and self.ask > 0

    @property
    def spread(self) -> Optional[float]:
        if not self.is_two_sided:
            return None
        return round(self.ask - self.bid, 4)

    @property
    def mid(self) -> Optional[float]:
        """Reference midpoint. For display only; never a fill price."""
        if not self.is_two_sided:
            return None
        return round((self.ask + self.bid) / 2, 4)

    def age_seconds(self, now: datetime) -> float:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return (now - self.observed_at).total_seconds()


class QuoteSource(Protocol):
    """Anything that can return the latest observed quote for a contract."""

    name: str

    def latest(self, contract: OptionContract) -> Optional[OptionQuote]: ...


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
