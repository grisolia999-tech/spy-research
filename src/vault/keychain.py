"""Secret storage in the macOS Keychain (service `schwab-api`).

Secrets (app key, app secret, OAuth tokens) live only here. Nothing secret goes into env.sh,
the repository, a log, or a chat. Reads and writes both go through /usr/bin/security so the
item's access list is the same program in both directions and launchd jobs read it without a
prompt.

Writes use `security -i` (commands on stdin) so the secret never appears in the process
argument list. Values are base64-wrapped before storage so quotes and braces in a JSON token
blob cannot break the command parser; `get` unwraps them.

This module was written without access to a Mac. Run the self-test once on the Mac Pro:
    python3 src/vault/keychain.py selftest
"""
from __future__ import annotations

import base64
import getpass
import re
import subprocess
import sys
from typing import Dict, Optional, Protocol

SERVICE = "schwab-api"
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_NOT_FOUND = 44  # `security` exit code: The specified item could not be found


class Vault(Protocol):
    def get(self, account: str) -> Optional[str]: ...
    def set(self, account: str, value: str) -> None: ...
    def delete(self, account: str) -> None: ...


class VaultError(RuntimeError):
    pass


class MemoryVault:
    """In-memory vault for tests."""

    def __init__(self, initial: Optional[Dict[str, str]] = None) -> None:
        self.items: Dict[str, str] = dict(initial or {})

    def get(self, account: str) -> Optional[str]:
        return self.items.get(account)

    def set(self, account: str, value: str) -> None:
        self.items[account] = value

    def delete(self, account: str) -> None:
        self.items.pop(account, None)


def _wrap(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _unwrap(stored: str) -> str:
    return base64.b64decode(stored.encode("ascii"), validate=True).decode("utf-8")


class KeychainVault:
    def __init__(self, service: str = SERVICE, runner=subprocess.run) -> None:
        if not _ACCOUNT_RE.match(service):
            raise ValueError("bad service name")
        self.service = service
        self._run = runner

    def get(self, account: str) -> Optional[str]:
        self._check(account)
        p = self._run(["security", "find-generic-password", "-s", self.service, "-a", account, "-w"],
                      capture_output=True, text=True)
        if p.returncode == _NOT_FOUND:
            return None
        if p.returncode != 0:
            raise VaultError(f"keychain read failed for {account!r}: exit {p.returncode}")
        try:
            return _unwrap(p.stdout.strip())
        except Exception as e:  # not one of ours
            raise VaultError(f"keychain item {account!r} is not in the expected format") from e

    def set(self, account: str, value: str) -> None:
        self._check(account)
        cmd = f"add-generic-password -U -s {self.service} -a {account} -w {_wrap(value)}\n"
        p = self._run(["security", "-i"], input=cmd, capture_output=True, text=True)
        if p.returncode != 0:
            raise VaultError(f"keychain write failed for {account!r}: exit {p.returncode}")
        if self.get(account) != value:
            raise VaultError(f"keychain write for {account!r} did not read back correctly")

    def delete(self, account: str) -> None:
        self._check(account)
        self._run(["security", "delete-generic-password", "-s", self.service, "-a", account],
                  capture_output=True, text=True)

    @staticmethod
    def _check(account: str) -> None:
        if not _ACCOUNT_RE.match(account):
            raise ValueError("bad account name")


def prompt_secret(label: str) -> str:
    """Read a secret without echo. Never printed, never logged."""
    return getpass.getpass(f"{label} (input hidden): ").strip()


def _selftest() -> int:
    import secrets as _secrets
    v = KeychainVault()
    probe = "selftest-" + _secrets.token_hex(8)
    v.set("selftest", probe)
    ok = v.get("selftest") == probe
    v.delete("selftest")
    gone = v.get("selftest") is None
    print("keychain write/read:", "OK" if ok else "FAILED", "| delete:", "OK" if gone else "FAILED")
    return 0 if ok and gone else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(_selftest())
    print(__doc__)
