import json
import unittest
import urllib.error
from datetime import date, datetime, timezone

from broker.interface import BrokerError, OrderBlocked, OrderRequest, OrderState
from broker.paper_orders import PaperBroker
from broker.schwab_orders import SchwabBroker, order_body
from data.quotes import OptionContract, OptionQuote, Right
from paper.sim import FeeModel, OrderSide, SlippageModel

C = OptionContract("SPY", date(2026, 10, 30), 500.0, Right.CALL)
NOW = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)


def quote(bid=1.00, ask=1.05, size=10):
    return OptionQuote(C, bid, ask, size, size, NOW, "TEST")


class PaperBrokerTests(unittest.TestCase):
    def make(self, q):
        return PaperBroker(lambda c: q, FeeModel(0.65), SlippageModel(1), lambda: NOW)

    def test_buy_fills_at_ask_plus_slippage_and_tracks_position(self):
        b = self.make(quote())
        r = b.place_order(OrderRequest(C, OrderSide.BUY, 2, 1.10))
        self.assertEqual((r.state, r.filled_quantity, r.avg_fill_price, r.kind), (OrderState.FILLED, 2, 1.06, "SIMULATED"))
        self.assertEqual(b.positions()[0].quantity, 2)

    def test_limit_below_price_rests_and_can_be_cancelled(self):
        b = self.make(quote())
        r = b.place_order(OrderRequest(C, OrderSide.BUY, 1, 1.00))
        self.assertEqual(r.state, OrderState.WORKING)
        self.assertEqual(b.cancel_all(), [r.order_id])
        self.assertEqual(b.order_status(r.order_id).state, OrderState.CANCELED)

    def test_cannot_sell_more_than_held(self):
        b = self.make(quote())
        self.assertEqual(b.place_order(OrderRequest(C, OrderSide.SELL, 1, 0.50)).state, OrderState.REJECTED)

    def test_sell_after_buy_closes_position(self):
        b = self.make(quote())
        b.place_order(OrderRequest(C, OrderSide.BUY, 1, 1.10))
        r = b.place_order(OrderRequest(C, OrderSide.SELL, 1, 0.90))
        self.assertEqual((r.state, r.avg_fill_price), (OrderState.FILLED, 0.99))
        self.assertEqual(b.positions(), [])

    def test_no_quote_rests(self):
        self.assertEqual(self.make(None).place_order(OrderRequest(C, OrderSide.BUY, 1, 1.10)).state, OrderState.WORKING)


class FakeResp:
    def __init__(self, body=None, status=200, headers=None):
        self._b = json.dumps(body).encode() if body is not None else b""
        self.status, self.headers = status, headers or {}
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeAuth:
    def access_token(self, now=None): return "TOK"


class SchwabBrokerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.gate_calls = 0

    def opener(self, req, timeout=0):
        self.calls.append((req.get_method(), req.full_url, req.data))
        url = req.full_url
        if url.endswith("/accounts/accountNumbers"):
            return FakeResp([{"accountNumber": "X", "hashValue": "HASH1"}])
        if req.get_method() == "POST":
            return FakeResp(None, 201, {"Location": "https://api.schwabapi.com/trader/v1/accounts/HASH1/orders/12345"})
        if req.get_method() == "DELETE":
            return FakeResp(None, 200)
        if "/orders?" in url:
            return FakeResp([{"orderId": 7, "status": "WORKING"}, {"orderId": 8, "status": "FILLED"}])
        if "fields=positions" in url:
            return FakeResp({"securitiesAccount": {"positions": [
                {"longQuantity": 2, "instrument": {"assetType": "OPTION", "symbol": "SPY   261030C00500000"}},
                {"longQuantity": 5, "instrument": {"assetType": "EQUITY", "symbol": "SPY"}}]}})
        raise AssertionError(url)

    def broker(self, allow=True):
        def gate(req):
            self.gate_calls += 1
            if not allow:
                raise OrderBlocked("switch is off")
        return SchwabBroker(FakeAuth(), gate, self.opener, lambda: NOW)

    def test_order_body_shape(self):
        b = order_body(OrderRequest(C, OrderSide.BUY, 1, 1.1))
        leg = b["orderLegCollection"][0]
        self.assertEqual((b["orderType"], b["price"], leg["instruction"], leg["instrument"]["symbol"]),
                         ("LIMIT", "1.10", "BUY_TO_OPEN", "SPY   261030C00500000"))

    def test_blocked_gate_sends_nothing(self):
        with self.assertRaises(OrderBlocked):
            self.broker(allow=False).place_order(OrderRequest(C, OrderSide.BUY, 1, 1.1))
        self.assertEqual(self.calls, [])

    def test_place_order_gated_then_posts_and_reads_id(self):
        r = self.broker().place_order(OrderRequest(C, OrderSide.BUY, 1, 1.1))
        self.assertEqual((r.order_id, self.gate_calls), ("12345", 1))
        self.assertTrue(self.calls[-1][1].endswith("/accounts/HASH1/orders"))

    def test_cancel_all_bypasses_gate_and_cancels_only_open(self):
        ids = self.broker(allow=False).cancel_all()
        self.assertEqual((ids, self.gate_calls), (["7"], 0))

    def test_positions_options_only(self):
        p = self.broker().positions()
        self.assertEqual((len(p), p[0].quantity, p[0].contract), (1, 2, C))

    def test_http_error_raises_broker_error(self):
        def bad(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, None)
        b = SchwabBroker(FakeAuth(), lambda r: None, bad, lambda: NOW)
        with self.assertRaises(BrokerError):
            b.positions()

    def test_gate_required(self):
        with self.assertRaises(ValueError):
            SchwabBroker(FakeAuth(), None)


class StatusMappingTests(unittest.TestCase):
    def test_unlisted_and_waiting_statuses_stay_open(self):
        from broker.schwab_orders import parse_order
        for st in ("AWAITING_MANUAL_REVIEW", "PENDING_ACKNOWLEDGEMENT", "AWAITING_STOP_CONDITION", "NEW", "SOMETHING_NEW"):
            self.assertTrue(parse_order({"orderId": 1, "status": st}).state.is_open, st)
        for st, want in (("FILLED", OrderState.FILLED), ("CANCELED", OrderState.CANCELED), ("REPLACED", OrderState.CANCELED),
                         ("REJECTED", OrderState.REJECTED), ("EXPIRED", OrderState.EXPIRED)):
            self.assertEqual(parse_order({"orderId": 1, "status": st}).state, want)


if __name__ == "__main__":
    unittest.main()
