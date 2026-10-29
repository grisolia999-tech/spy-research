import json
import subprocess
import unittest

from schwabapi.auth import SchwabAuth, SchwabLoginRequired, VaultTokenStore
from vault.keychain import KeychainVault, MemoryVault, VaultError, _wrap


class FakeSecurity:
    """Stands in for /usr/bin/security: remembers items written through `security -i`."""

    def __init__(self):
        self.items = {}
        self.calls = []

    def __call__(self, argv, input=None, capture_output=True, text=True):
        self.calls.append((list(argv), input))
        if argv[:2] == ["security", "-i"]:
            parts = input.split()
            acct = parts[parts.index("-a") + 1]
            self.items[acct] = parts[parts.index("-w") + 1]
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "find-generic-password":
            acct = argv[argv.index("-a") + 1]
            if acct not in self.items:
                return subprocess.CompletedProcess(argv, 44, "", "not found")
            return subprocess.CompletedProcess(argv, 0, self.items[acct] + "\n", "")
        if argv[1] == "delete-generic-password":
            self.items.pop(argv[argv.index("-a") + 1], None)
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)


class KeychainVaultTests(unittest.TestCase):
    def test_roundtrip_and_secret_never_in_argv(self):
        fake = FakeSecurity()
        v = KeychainVault(runner=fake)
        secret = 'tok{"a":"b c"}'
        v.set("tokens", secret)
        self.assertEqual(v.get("tokens"), secret)
        for argv, _ in fake.calls:
            self.assertNotIn(secret, " ".join(argv))
            self.assertNotIn(_wrap(secret), " ".join(argv))
        stdin = [i for _, i in fake.calls if i][0]
        self.assertIn(_wrap(secret), stdin)  # only wrapped form, and only on stdin
        self.assertNotIn(secret, stdin)

    def test_missing_item_is_none_and_delete_removes(self):
        fake = FakeSecurity()
        v = KeychainVault(runner=fake)
        self.assertIsNone(v.get("nope"))
        v.set("x", "1")
        v.delete("x")
        self.assertIsNone(v.get("x"))

    def test_read_failure_raises_instead_of_returning_none(self):
        def broken(argv, input=None, capture_output=True, text=True):
            return subprocess.CompletedProcess(argv, 1, "", "denied")
        with self.assertRaises(VaultError):
            KeychainVault(runner=broken).get("tokens")

    def test_bad_names_rejected(self):
        with self.assertRaises(ValueError):
            KeychainVault(runner=FakeSecurity()).get("a; rm -rf")


class VaultAuthTests(unittest.TestCase):
    def test_from_vault_requires_key_and_secret(self):
        with self.assertRaises(ValueError):
            SchwabAuth.from_vault(MemoryVault())

    def test_from_vault_uses_default_callback_and_vault_token_store(self):
        v = MemoryVault({"app_key": "k", "app_secret": "s"})
        a = SchwabAuth.from_vault(v)
        self.assertEqual(a.callback_url, "https://127.0.0.1")
        self.assertIsInstance(a.store, VaultTokenStore)
        with self.assertRaises(SchwabLoginRequired):
            a.access_token(1.0)
        a.store.save({"access_token": "A", "refresh_token": "R", "access_expires_at": 9e9, "refresh_issued_at": 1.0})
        self.assertEqual(a.access_token(2.0), "A")
        self.assertEqual(json.loads(v.items["tokens"])["refresh_token"], "R")
        self.assertEqual(a.seconds_until_refresh_expiry(1.0), 7 * 86400)


if __name__ == "__main__":
    unittest.main()
