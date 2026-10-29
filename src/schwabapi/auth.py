"""Schwab OAuth for the Trader API (market data and, when armed, orders).

Secrets live in the macOS Keychain (see vault/keychain.py). The env/file variant exists for
tests and for a one-time import; it is not how the trading system runs.

Token facts (Schwab developer docs and wrappers; confirm with `--probe` on first use):
  - access token: about 30 minutes, refreshed automatically here
  - refresh token: 7 days from issue; after that a browser login is required (`./reauth`)
Nothing here ever returns a stale or partial token silently: failures raise.
"""
from __future__ import annotations

import base64
import json
import os
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional, Protocol

AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
REFRESH_LIFETIME_S = 7 * 86400
ACCESS_MARGIN_S = 120


class SchwabLoginRequired(Exception):
    """The refresh token is missing, expired, or rejected. A browser login is needed."""


class SchwabTemporaryError(Exception):
    """Network or server problem. Not a login problem; try again later."""


class TokenStore(Protocol):
    def load(self) -> Optional[dict]: ...
    def save(self, tokens: dict) -> None: ...
    def describe(self) -> str: ...


class FileTokenStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text())

    def save(self, tokens: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tokens))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def describe(self) -> str:
        return str(self.path)


class VaultTokenStore:
    def __init__(self, vault, account: str = "tokens") -> None:
        self.vault, self.account = vault, account

    def load(self) -> Optional[dict]:
        raw = self.vault.get(self.account)
        return json.loads(raw) if raw else None

    def save(self, tokens: dict) -> None:
        self.vault.set(self.account, json.dumps(tokens))

    def describe(self) -> str:
        return f"Keychain item {self.account!r}"


class SchwabAuth:
    def __init__(self, app_key: str, app_secret: str, callback_url: str, store: TokenStore,
                 opener: Optional[Callable] = None) -> None:
        if not (app_key and app_secret and callback_url):
            raise ValueError("Schwab app key, secret and callback URL are all required")
        self.app_key, self.app_secret, self.callback_url = app_key, app_secret, callback_url
        self.store = store
        self._open = opener or urllib.request.urlopen

    # -- construction ---------------------------------------------------------------
    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None, opener: Optional[Callable] = None) -> "SchwabAuth":
        env = os.environ if env is None else env
        missing = [k for k in ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "SCHWAB_CALLBACK_URL") if not env.get(k)]
        if missing:
            raise ValueError("missing environment variables: " + ", ".join(missing))
        path = env.get("SCHWAB_TOKEN_PATH") or str(Path.home() / ".spy-research" / "schwab_tokens.json")
        return cls(env["SCHWAB_APP_KEY"], env["SCHWAB_APP_SECRET"], env["SCHWAB_CALLBACK_URL"],
                   FileTokenStore(Path(path)), opener)

    @classmethod
    def from_vault(cls, vault=None, opener: Optional[Callable] = None) -> "SchwabAuth":
        if vault is None:
            from vault.keychain import KeychainVault
            vault = KeychainVault()
        key, secret = vault.get("app_key"), vault.get("app_secret")
        if not key or not secret:
            raise ValueError("Schwab app key/secret not in the Keychain. Run ./reauth to store them.")
        callback = vault.get("callback_url") or "https://127.0.0.1"
        return cls(key, secret, callback, VaultTokenStore(vault), opener)

    @property
    def token_path(self) -> str:
        return self.store.describe()

    # -- login ----------------------------------------------------------------------
    def authorize_url(self) -> str:
        q = urllib.parse.urlencode({"client_id": self.app_key, "redirect_uri": self.callback_url})
        return f"{AUTH_URL}?{q}"

    def exchange_redirect(self, redirect_url: str, now: Optional[float] = None) -> None:
        """Exchange the one-time code from the browser's final address for tokens."""
        now = _time.time() if now is None else now
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url.strip()).query)
        code = (qs.get("code") or [""])[0]
        if not code:
            raise ValueError("no 'code' found in that URL; paste the full address from the browser bar")
        body = self._token_call({"grant_type": "authorization_code", "code": code,
                                 "redirect_uri": self.callback_url})
        if "access_token" not in body or "refresh_token" not in body:
            raise ValueError("token response missing access_token or refresh_token")
        self.store.save({
            "access_token": body["access_token"],
            "refresh_token": body["refresh_token"],
            "access_expires_at": now + float(body.get("expires_in", 1800)),
            "refresh_issued_at": now,
        })

    # -- use ------------------------------------------------------------------------
    def access_token(self, now: Optional[float] = None) -> str:
        now = _time.time() if now is None else now
        t = self.store.load()
        if not t:
            raise SchwabLoginRequired("no saved tokens")
        if now < t["access_expires_at"] - ACCESS_MARGIN_S:
            return t["access_token"]
        if now >= t["refresh_issued_at"] + REFRESH_LIFETIME_S:
            raise SchwabLoginRequired("refresh token is older than 7 days")
        try:
            body = self._token_call({"grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
        except urllib.error.HTTPError as e:
            if e.code in (400, 401):
                raise SchwabLoginRequired(f"refresh rejected (HTTP {e.code})") from e
            raise SchwabTemporaryError(f"token endpoint HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise SchwabTemporaryError(f"token endpoint unreachable: {e}") from e
        if "access_token" not in body:
            raise SchwabTemporaryError("token response missing access_token")
        old_refresh = t["refresh_token"]
        new_refresh = body.get("refresh_token", old_refresh)
        t = dict(t, access_token=body["access_token"], refresh_token=new_refresh,
                 access_expires_at=now + float(body.get("expires_in", 1800)))
        if new_refresh != old_refresh:
            t["refresh_issued_at"] = now  # a genuinely new refresh token restarts the 7-day clock
        self.store.save(t)
        return t["access_token"]

    def refresh_expires_at(self) -> Optional[float]:
        t = self.store.load()
        return None if not t else t["refresh_issued_at"] + REFRESH_LIFETIME_S

    def seconds_until_refresh_expiry(self, now: Optional[float] = None) -> Optional[float]:
        exp = self.refresh_expires_at()
        return None if exp is None else exp - (_time.time() if now is None else now)

    # -- internals ------------------------------------------------------------------
    def _token_call(self, form: Dict[str, str]) -> dict:
        basic = base64.b64encode(f"{self.app_key}:{self.app_secret}".encode()).decode()
        req = urllib.request.Request(
            TOKEN_URL, data=urllib.parse.urlencode(form).encode(), method="POST",
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self._open(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if form.get("grant_type") == "authorization_code":
                raise ValueError(f"login code rejected by Schwab (HTTP {e.code}); codes are single-use, "
                                 f"start again from the login URL") from e
            raise
