"""SAMPLE DATA ONLY.

Every quote produced here is SYNTHETIC. Prices, sizes and timestamps are placeholders chosen
to exercise code paths. They are not market observations and must never be used to evaluate
strategy performance. The source field is set to SAMPLE_SOURCE so downstream code and
journals can see where a quote came from.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional

from data.quotes import OptionContract, OptionQuote, Right

SAMPLE_SOURCE = "SAMPLE_SYNTHETIC"

# Fixed synthetic session clock so tests are deterministic.
SAMPLE_NOW = datetime(2026, 10, 2, 14, 30, 0, tzinfo=timezone.utc)  # 10:30 ET on a weekday

SAMPLE_CONTRACTS = (
    OptionContract("SPY", date(2026, 10, 2), 500.0, Right.CALL),
    OptionContract("SPY", date(2026, 10, 2), 500.0, Right.PUT),
)


def sample_quote(
    contract: OptionContract = SAMPLE_CONTRACTS[0],
    bid: Optional[float] = 1.20,
    ask: Optional[float] = 1.26,
    bid_size: int = 50,
    ask_size: int = 40,
    age_seconds: float = 1.0,
    now: datetime = SAMPLE_NOW,
) -> OptionQuote:
    """Build a labeled synthetic quote. Arguments exist so tests can shape edge cases."""
    return OptionQuote(
        contract=contract,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        observed_at=now - timedelta(seconds=age_seconds),
        source=SAMPLE_SOURCE,
        underlying_price=500.0,
    )


class SampleQuoteSource:
    """In-memory QuoteSource holding synthetic quotes. Not a market data feed."""

    name = SAMPLE_SOURCE

    def __init__(self, quotes: Optional[Dict[OptionContract, OptionQuote]] = None) -> None:
        self._quotes = dict(quotes) if quotes is not None else {c: sample_quote(c) for c in SAMPLE_CONTRACTS}

    def latest(self, contract: OptionContract) -> Optional[OptionQuote]:
        return self._quotes.get(contract)

    def put(self, quote: OptionQuote) -> None:
        self._quotes[quote.contract] = quote
