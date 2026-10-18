"""Tests use a fake HTTP opener. No network. The response shape follows Schwab's public API as
documented by open-source wrappers and must be confirmed once with `run_paper.py --probe`."""
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path

from data.quotes import OptionContract, Right
from data.schwab_quotes import (SCHWAB_SOURCE, SchwabAuth, SchwabLoginRequired, SchwabQuoteSource,
                                occ_symbol, parse_option_quote)

CALL = OptionContract("SPY", date(2026, 10, 19), 500.0, Right.CALL)
PUT = OptionContract("SPY", date(2026, 10, 19), 500.0, Right.PUT)
ENV = {"SCHWAB_APP_KEY": "key", "SCHWAB_APP_SECRET": "secret", "SCHWAB_CALLBACK_URL": "https://127.0.0.1"}
T0 = __import__("time").time()  # real clock so 7-day refresh logic sees fresh tokens


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self):
        self.calls = []
        self.token_responses = []
        self.quote_response = {}
        self.fail_quotes_with = None

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        if req.full_url.startswith("https://api.schwabapi.com/v1/oauth/token"):
            status, body = self.token_responses.pop(0)
            if status != 200:
                raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(json.dumps(body).encode()))
            return FakeResponse(json.dumps(body).encode())
        if self.fail_quotes_with:
            raise urllib.error.HTTPError(req.full_url, self.fail_quotes_with, "err", {}, io.BytesIO(b"{}"))
        return FakeResponse(json.dumps(self.quote_response).encode())


def auth_with(opener, tmp):
    env = dict(ENV, SCHWAB_TOKEN_PATH=str(Path(tmp) / "tok.json"))
    return SchwabAuth.from_env(env, opener=opener)


class SymbolTests(unittest.TestCase):
    def test_occ_symbol_format(self):
        self.assertEqual(occ_symbol(CALL), "SPY   261019C00500000")
        self.assertEqual(occ_symbol(OptionContract("SPY", date(2026, 10, 19), 497.5, Right.PUT)), "SPY   261019P00497500")


class AuthTests(unittest.TestCase):
    def test_missing_env_blocks(self):
        with self.assertRaises(Exception):
            SchwabAuth.from_env({}, opener=FakeOpener())

    def test_no_tokens_requires_login(self):
        with tempfile.TemporaryDirectory() as d:
            a = auth_with(FakeOpener(), d)
            with self.assertRaises(SchwabLoginRequired):
                a.access_token(T0)

    def test_login_exchange_saves_tokens_and_refreshes_when_expiring(self):
        op = FakeOpener()
        op.token_responses = [
            (200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 1800}),
            (200, {"access_token": "A2", "expires_in": 1800}),
        ]
        with tempfile.TemporaryDirectory() as d:
            a = auth_with(op, d)
            self.assertIn("client_id=key", a.authorize_url())
            a.exchange_redirect("https://127.0.0.1/?code=ABC%40&session=xyz", now=T0)
            self.assertEqual(a.access_token(T0 + 60), "A1")
            self.assertEqual(len(op.calls), 1)
            # 29 minutes later the access token is inside the refresh margin -> refresh call
            self.assertEqual(a.access_token(T0 + 1740), "A2")
            self.assertEqual(len(op.calls), 2)
            body = op.calls[1].data.decode()
            self.assertIn("grant_type=refresh_token", body)
            self.assertIn("refresh_token=R1", body)
            # token file persisted and reloads
            b = auth_with(op, d)
            self.assertEqual(b.access_token(T0 + 1800), "A2")
            saved = json.loads((Path(d) / "tok.json").read_text())
            self.assertEqual(saved["refresh_token"], "R1")

    def test_seven_day_old_refresh_token_requires_login(self):
        op = FakeOpener()
        op.token_responses = [(200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 1800})]
        with tempfile.TemporaryDirectory() as d:
            a = auth_with(op, d)
            a.exchange_redirect("https://127.0.0.1/?code=ABC", now=T0)
            with self.assertRaises(SchwabLoginRequired):
                a.access_token(T0 + 7 * 86400)

    def test_rejected_refresh_requires_login(self):
        op = FakeOpener()
        op.token_responses = [
            (200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 1800}),
            (401, {"error": "invalid_grant"}),
        ]
        with tempfile.TemporaryDirectory() as d:
            a = auth_with(op, d)
            a.exchange_redirect("https://127.0.0.1/?code=ABC", now=T0)
            with self.assertRaises(SchwabLoginRequired):
                a.access_token(T0 + 1800)


RAW = {
    "SPY": {"assetMainType": "EQUITY", "quote": {"lastPrice": 500.25, "quoteTime": 1760000000000}},
    "SPY   261019C00500000": {"assetMainType": "OPTION", "quote": {
        "bidPrice": 1.20, "askPrice": 1.26, "bidSize": 50, "askSize": 40,
        "quoteTime": 1760000000500, "underlyingPrice": 500.25}},
    "SPY   261019P00500000": {"assetMainType": "OPTION", "quote": {
        "bidPrice": 0, "askPrice": 1.10, "bidSize": 0, "askSize": 10, "quoteTime": 1760000000500}},
}


class ParseAndSourceTests(unittest.TestCase):
    def test_parse_uses_schwab_quote_time_and_labels_source(self):
        received = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)
        q = parse_option_quote(CALL, RAW["SPY   261019C00500000"], received)
        self.assertEqual((q.bid, q.ask, q.bid_size, q.ask_size), (1.20, 1.26, 50, 40))
        self.assertEqual(q.source, SCHWAB_SOURCE)
        self.assertEqual(q.observed_at, datetime.fromtimestamp(1760000000.5, tz=timezone.utc))
        self.assertEqual(q.underlying_price, 500.25)

    def test_parse_falls_back_to_received_time_and_returns_none_without_prices(self):
        received = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)
        q = parse_option_quote(CALL, {"quote": {"bidPrice": 1.0, "askPrice": 1.1}}, received)
        self.assertEqual(q.observed_at, received)
        self.assertIsNone(parse_option_quote(CALL, {"quote": {}}, received))

    def test_refresh_snapshots_records_and_serves_latest(self):
        op = FakeOpener()
        op.token_responses = [(200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 1800})]
        op.quote_response = RAW
        with tempfile.TemporaryDirectory() as d:
            a = auth_with(op, d)
            a.exchange_redirect("https://127.0.0.1/?code=ABC", now=T0)
            rec = Path(d) / "rec.jsonl"
            src = SchwabQuoteSource(a, [CALL, PUT], record_path=rec,
                                    clock=lambda: datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc))
            self.assertEqual(src.symbols(), ["SPY", "SPY   261019C00500000", "SPY   261019P00500000"])
            self.assertIsNone(src.latest(CALL))  # nothing until refresh
            snap = src.refresh()
            self.assertEqual(set(snap), {CALL, PUT})
            self.assertEqual(src.latest(CALL).ask, 1.26)
            self.assertFalse(src.latest(PUT).is_two_sided)  # zero bid -> not tradeable
            self.assertEqual(src.underlying_last, 500.25)
            req = [c for c in op.calls if "marketdata" in c.full_url][0]
            self.assertIn("symbols=SPY%2CSPY+++261019C00500000", req.full_url)
            self.assertEqual(req.get_header("Authorization"), "Bearer A1")
            lines = rec.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["response"]["SPY"]["quote"]["lastPrice"], 500.25)

    def test_401_on_quotes_surfaces_login_required(self):
        op = FakeOpener()
        op.token_responses = [(200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 1800})]
        op.fail_quotes_with = 401
        with tempfile.TemporaryDirectory() as d:
            a = auth_with(op, d)
            a.exchange_redirect("https://127.0.0.1/?code=ABC", now=T0)
            src = SchwabQuoteSource(a, [CALL])
            with self.assertRaises(SchwabLoginRequired):
                src.refresh()


if __name__ == "__main__":
    unittest.main()
