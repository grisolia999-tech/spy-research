"""Schwab Market Data quote source (read-only).

Polls GET https://api.schwabapi.com/marketdata/v1/quotes for the configured contracts and the
underlying, labels every quote SCHWAB_API, and appends each raw response to a JSON-lines file for
replay. This module only reads market data; it contains no order code.

Field names come from Schwab's published OpenAPI spec (Market Data v1, OptionResponse ->
QuoteOption): bidPrice, askPrice, bidSize, askSize, quoteTime (ms since epoch), underlyingPrice.
`parse_option_quote` is the only place that reads those names. Confirm once with
`run_paper.py --source schwab --probe`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from data.quotes import OptionContract, OptionQuote, utcnow
from schwabapi.auth import (SchwabAuth, SchwabLoginRequired, SchwabTemporaryError,  # noqa: F401 (re-export)
                            AUTH_URL, TOKEN_URL)

SCHWAB_SOURCE = "SCHWAB_API"
QUOTES_URL = "https://api.schwabapi.com/marketdata/v1/quotes"


def occ_symbol(c: OptionContract) -> str:
    """Schwab/OCC option symbol: root padded to 6, yymmdd, C/P, strike*1000 as 8 digits."""
    strike = int(round(c.strike * 1000))
    return f"{c.underlying:<6}{c.expiry:%y%m%d}{c.right.value[0]}{strike:08d}"


def _num(v) -> Optional[float]:
    return None if v is None else float(v)


def parse_option_quote(contract: OptionContract, raw: dict, received_at: datetime) -> Optional[OptionQuote]:
    """Map one Schwab option quote object to an OptionQuote, or None if it has no prices at all."""
    q = (raw or {}).get("quote") or {}
    bid, ask = _num(q.get("bidPrice")), _num(q.get("askPrice"))
    if bid is None and ask is None:
        return None
    qt = q.get("quoteTime")
    observed = datetime.fromtimestamp(qt / 1000.0, tz=timezone.utc) if qt else received_at
    if bid is not None and ask is not None and ask < bid:
        return None  # crossed quote: unusable, treated as no quote
    return OptionQuote(
        contract=contract, bid=bid, ask=ask,
        bid_size=int(q.get("bidSize") or 0), ask_size=int(q.get("askSize") or 0),
        observed_at=observed, source=SCHWAB_SOURCE, underlying_price=_num(q.get("underlyingPrice")))


class SchwabQuoteSource:
    name = SCHWAB_SOURCE

    def __init__(self, auth: SchwabAuth, contracts: List[OptionContract], underlying: str = "SPY",
                 record_path: Optional[Path] = None, clock: Callable[[], datetime] = utcnow,
                 opener: Optional[Callable] = None) -> None:
        self.auth, self.contracts, self.underlying = auth, list(contracts), underlying
        self.record_path = Path(record_path) if record_path else None
        self._clock = clock
        self._open = opener or auth._open  # same HTTP opener as auth, so tests inject one fake
        self._latest: Dict[OptionContract, OptionQuote] = {}
        self.last_raw: dict = {}
        self.underlying_last: Optional[float] = None
        self.underlying_observed_at: Optional[datetime] = None

    def symbols(self) -> List[str]:
        return [self.underlying] + [occ_symbol(c) for c in self.contracts]

    def latest(self, contract: OptionContract) -> Optional[OptionQuote]:
        return self._latest.get(contract)

    def refresh(self) -> Dict[OptionContract, OptionQuote]:
        received = self._clock()
        qs = urllib.parse.urlencode({"symbols": ",".join(self.symbols()), "fields": "quote"},
                                    quote_via=urllib.parse.quote_plus)
        req = urllib.request.Request(f"{QUOTES_URL}?{qs}", headers={
            "Authorization": f"Bearer {self.auth.access_token()}", "Accept": "application/json"})
        try:
            with self._open(req, timeout=15) as r:
                raw = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise SchwabLoginRequired("quotes endpoint rejected the access token (HTTP 401)") from e
            raise SchwabTemporaryError(f"quotes endpoint HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise SchwabTemporaryError(f"quotes endpoint unreachable: {e}") from e
        self.last_raw = raw
        self._record(received, raw)
        u = (raw.get(self.underlying) or {}).get("quote") or {}
        if u.get("lastPrice") is not None:
            self.underlying_last = float(u["lastPrice"])
            qt = u.get("quoteTime")
            self.underlying_observed_at = datetime.fromtimestamp(qt / 1000.0, tz=timezone.utc) if qt else received
        snap: Dict[OptionContract, OptionQuote] = {}
        for c in self.contracts:
            q = parse_option_quote(c, raw.get(occ_symbol(c)), received)
            if q is not None:
                snap[c] = q
                self._latest[c] = q
        return snap

    def _record(self, received: datetime, raw: dict) -> None:
        if not self.record_path:
            return
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        with self.record_path.open("a") as f:
            f.write(json.dumps({"received_at": received.isoformat(), "response": raw}) + "\n")
