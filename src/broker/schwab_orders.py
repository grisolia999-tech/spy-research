"""Schwab Trader API broker (REAL ORDERS when the gate allows).

Safety design:
  * `gate` is REQUIRED and is called immediately before every order HTTP request. It raises
    OrderBlocked unless the switch is `live` and every limit passes. No gate, no broker.
  * Cancels and read-only calls never go through the gate, so ./halt can always cancel.
  * Account numbers are never stored in the repo: the encrypted account hash is fetched from
    /accounts/accountNumbers at runtime.

UNVERIFIED against Schwab's official Accounts-and-Trading spec (only public wrapper docs were
available when written). Re-check each item marked UNVERIFIED before the first live arm:
  - accountNumbers endpoint and field name `hashValue`
  - instruction names BUY_TO_OPEN / SELL_TO_CLOSE
  - order id taken from the `Location` header of the 201 response
  - status names, positions field layout, required query params of GET /orders
`python3 run_paper.py --probe-broker` (read-only) is meant to confirm these with real responses.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from broker.interface import (BrokerError, BrokerPosition, OrderRequest, OrderResult, OrderState)
from data.quotes import OptionContract, Right
from data.schwab_quotes import occ_symbol
from paper.sim import OrderSide
from schwabapi.auth import SchwabAuth, SchwabLoginRequired

TRADER_BASE = "https://api.schwabapi.com/trader/v1"
INSTRUCTION = {OrderSide.BUY: "BUY_TO_OPEN", OrderSide.SELL: "SELL_TO_CLOSE"}  # UNVERIFIED
_STATE = {  # UNVERIFIED mapping of Schwab status strings
    "FILLED": OrderState.FILLED, "CANCELED": OrderState.CANCELED, "REJECTED": OrderState.REJECTED,
    "EXPIRED": OrderState.EXPIRED, "WORKING": OrderState.WORKING, "QUEUED": OrderState.WORKING,
    "ACCEPTED": OrderState.WORKING, "PENDING_ACTIVATION": OrderState.WORKING,
    "AWAITING_PARENT_ORDER": OrderState.WORKING, "AWAITING_CONDITION": OrderState.WORKING,
    "PENDING_CANCEL": OrderState.WORKING, "PENDING_REPLACE": OrderState.WORKING,
    "NEW": OrderState.WORKING, "REPLACED": OrderState.CANCELED,
}


def order_body(req: OrderRequest) -> dict:
    """Schwab order JSON for a single-leg DAY limit option order."""
    return {
        "orderType": "LIMIT", "session": "NORMAL", "duration": "DAY",
        "orderStrategyType": "SINGLE", "price": f"{req.limit_price:.2f}",
        "orderLegCollection": [{
            "instruction": INSTRUCTION[req.side], "quantity": req.quantity,
            "instrument": {"assetType": "OPTION", "symbol": occ_symbol(req.contract)},
        }],
    }


def parse_order(o: dict) -> OrderResult:
    state = _STATE.get(str(o.get("status", "")).upper(), OrderState.UNKNOWN)
    filled = int(o.get("filledQuantity") or 0)
    if state is OrderState.WORKING and filled > 0:
        state = OrderState.PARTIAL
    price = None
    prices = [(leg.get("price"), leg.get("quantity")) for act in (o.get("orderActivityCollection") or [])
              for leg in (act.get("executionLegs") or [])]
    qty = sum(float(q or 0) for p, q in prices if p is not None)
    if qty:
        price = round(sum(float(p) * float(q or 0) for p, q in prices if p is not None) / qty, 4)
    return OrderResult(str(o.get("orderId", "")), state, filled, price,
                       message=str(o.get("statusDescription") or ""), kind="BROKER")


def _contract_from_symbol(sym: str) -> Optional[OptionContract]:
    s = sym.ljust(21)
    try:
        root, ymd, cp, k = s[:6].strip(), s[6:12], s[12], s[13:21]
        expiry = datetime.strptime(ymd, "%y%m%d").date()
        return OptionContract(root, expiry, int(k) / 1000.0, Right.CALL if cp == "C" else Right.PUT)
    except (ValueError, IndexError):
        return None


class SchwabBroker:
    mode = "live"

    def __init__(self, auth: SchwabAuth, gate: Callable[[OrderRequest], None],
                 opener: Optional[Callable] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        if gate is None:
            raise ValueError("SchwabBroker requires a gate")
        self._auth, self._gate, self._clock = auth, gate, clock
        self._open = opener or urllib.request.urlopen
        self._hash: Optional[str] = None

    # -- orders ---------------------------------------------------------------------
    def place_order(self, req: OrderRequest) -> OrderResult:
        self._gate(req)  # raises OrderBlocked; nothing below runs
        h = self._account_hash()
        status, headers, _ = self._call("POST", f"/accounts/{h}/orders", json.dumps(order_body(req)).encode())
        if status != 201:
            raise BrokerError(f"unexpected status {status} placing order")
        loc = headers.get("Location") or headers.get("location") or ""
        oid = loc.rstrip("/").rsplit("/", 1)[-1]  # UNVERIFIED
        if not oid:
            raise BrokerError("order accepted but no order id in Location header; check Schwab before retrying")
        return OrderResult(oid, OrderState.WORKING, message="submitted", kind="BROKER")

    def cancel_order(self, order_id: str) -> OrderResult:
        h = self._account_hash()
        self._call("DELETE", f"/accounts/{h}/orders/{urllib.parse.quote(order_id)}")
        return OrderResult(order_id, OrderState.CANCELED, message="cancel requested", kind="BROKER")

    def cancel_all(self) -> List[str]:
        ids, failures = [], []
        for o in self.open_orders():
            try:
                self.cancel_order(o.order_id)
                ids.append(o.order_id)
            except BrokerError as e:
                failures.append(f"{o.order_id}: {e}")
        if failures:
            raise BrokerError("cancel failed for " + "; ".join(failures))
        return ids

    def order_status(self, order_id: str) -> OrderResult:
        h = self._account_hash()
        _, _, body = self._call("GET", f"/accounts/{h}/orders/{urllib.parse.quote(order_id)}")
        return parse_order(body)

    def open_orders(self) -> List[OrderResult]:
        h = self._account_hash()
        now = self._clock()
        q = urllib.parse.urlencode({  # UNVERIFIED: both times are required by the endpoint
            "fromEnteredTime": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "toEnteredTime": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        })
        _, _, body = self._call("GET", f"/accounts/{h}/orders?{q}")
        return [r for r in (parse_order(o) for o in (body or [])) if r.state.is_open]

    def positions(self) -> List[BrokerPosition]:
        h = self._account_hash()
        _, _, body = self._call("GET", f"/accounts/{h}?fields=positions")
        out = []
        for p in ((body or {}).get("securitiesAccount") or {}).get("positions") or []:  # UNVERIFIED layout
            ins = p.get("instrument") or {}
            if ins.get("assetType") != "OPTION":
                continue
            c = _contract_from_symbol(str(ins.get("symbol", "")))
            qty = int(float(p.get("longQuantity") or 0))
            if c and qty > 0:
                out.append(BrokerPosition(c, qty))
        return out

    # -- internals ------------------------------------------------------------------
    def _account_hash(self) -> str:
        if self._hash:
            return self._hash
        _, _, body = self._call("GET", "/accounts/accountNumbers")  # UNVERIFIED
        if not body or not body[0].get("hashValue"):
            raise BrokerError("no account hash returned by Schwab")
        if len(body) > 1:
            raise BrokerError("more than one account; choose one explicitly before trading")
        self._hash = body[0]["hashValue"]
        return self._hash

    def _call(self, method: str, path: str, data: Optional[bytes] = None):
        try:
            token = self._auth.access_token()
        except SchwabLoginRequired as e:
            raise BrokerError(f"login required: {e}") from e
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(TRADER_BASE + path, data=data, method=method, headers=headers)
        try:
            with self._open(req, timeout=20) as r:
                raw = r.read().decode() or ""
                status = getattr(r, "status", 200)
                hdrs = dict(r.headers.items()) if getattr(r, "headers", None) else {}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
            raise BrokerError(f"{method} {path.split('?')[0]} -> HTTP {e.code} {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            raise BrokerError(f"{method} {path.split('?')[0]} unreachable: {e}") from e
        return status, hdrs, (json.loads(raw) if raw else None)
