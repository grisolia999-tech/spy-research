"""./reauth: store Schwab credentials in the Keychain and walk through the weekly browser login.

Nothing secret is printed or kept in shell history. The app key/secret are read with getpass or
imported from the old ~/.spy-research/env.sh (the file is left alone; delete it yourself after).
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path

from schwabapi.auth import SchwabAuth
from vault.keychain import KeychainVault

ENV_FILE = Path.home() / ".spy-research" / "env.sh"


def _from_env_file() -> dict:
    out = {}
    if ENV_FILE.exists():
        for name, key in (("SCHWAB_APP_KEY", "app_key"), ("SCHWAB_APP_SECRET", "app_secret"),
                          ("SCHWAB_CALLBACK_URL", "callback_url")):
            m = re.search(rf"^\s*(?:export\s+)?{name}=['\"]?([^'\"\n]+)['\"]?\s*$", ENV_FILE.read_text(), re.M)
            if m:
                out[key] = m.group(1)
    return out


def main() -> int:
    v = KeychainVault()
    if not (v.get("app_key") and v.get("app_secret")):
        found = _from_env_file()
        if "app_key" in found and "app_secret" in found and \
                input(f"Import app key/secret from {ENV_FILE} into the Keychain? [y/N] ").strip().lower() == "y":
            for k, val in found.items():
                v.set(k, val)
            print("Imported. You can delete that env file once this works.")
        else:
            v.set("app_key", getpass("Schwab app key (hidden): ").strip())
            v.set("app_secret", getpass("Schwab app secret (hidden): ").strip())
            v.set("callback_url", input("Callback URL [https://127.0.0.1]: ").strip() or "https://127.0.0.1")
    auth = SchwabAuth.from_vault(v)
    url = auth.authorize_url()
    print("\n1. Your browser will open Schwab's login. Log in and approve.\n"
          "2. The final page will fail to load (that is expected). Copy the FULL address from the address bar.\n"
          "3. Come back here and press Return IMMEDIATELY; the code works once and expires within a minute.\n")
    try:
        subprocess.run(["open", url], check=False)
    except OSError:
        print("Open this address yourself:\n" + url)
    input("When the page has failed to load: click the address bar, select all, COPY it (Cmd+C),\n"
          "then come back here and press Return. ")
    redirect = ""
    try:
        redirect = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    if "code=" not in redirect:
        redirect = getpass("Clipboard had no login address. Paste it here now (hidden): ")
    auth.exchange_redirect(redirect)
    exp = auth.refresh_expires_at()
    print("Saved to the Keychain. Login valid until "
          + datetime.fromtimestamp(exp, timezone.utc).astimezone().strftime("%a %d %b %H:%M")
          + ". You will get a notification 24h before.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
