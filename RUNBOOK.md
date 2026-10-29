# RUNBOOK: arm, watch, stop, renew

Switch states (`config/switch.json`, changed only by you with `./arm` / `./halt`):
`off` no orders, data and signals still run | `paper` simulated orders, logged | `live` real Schwab orders.
Every start, crash or deploy comes back **off**. `live` also expires at 16:00 New York time.
Paper results never establish live profitability.

## One-time setup (Mac Pro)
1. Clone the repo, `cd` into it, check `python3 --version` is 3.11+.
2. `python3 -m unittest discover -t . -s tests` must say OK. Then `python3 src/vault/keychain.py selftest`.
3. `./reauth` (imports key/secret to the Keychain, then the browser login). Delete `~/.spy-research/env.sh` after it works.
4. `scripts/install_mac.sh` puts `Trading START.command` and `Trading STOP.command` on the Desktop.
5. Read `config/limits.json`. Change what you disagree with. Only then set `"owner_reviewed": true`.
6. Schwab order format: checked against the official spec on 2026-10-29. A few items remain UNVERIFIED (listed at the top of `src/broker/schwab_orders.py`); confirm them with the first real responses before the first live arm.

## Daily
- **Start:** double-click `Trading START` (engine up, switch OFF).
- **Arm:** in Terminal, in the repo: `./arm paper` or `./arm live`. Live runs the tests, checks limits and login, then asks you to type `CONFIRM`.
- **Watch:** `./status` (switch, ledger check, login time left, last events). Full record: `logs/trades.jsonl` (one line per attempt, block, fill, switch change; each line carries the checks that passed or failed; edits are detected).
- **Stop:** double-click `Trading STOP`, or `./halt` in Terminal. Switch goes OFF first, then open orders are cancelled. **Halt does not close a position.** If one is open, sell it in Schwab yourself.

## Limits (enforced before every order, all modes)
Max contracts per order and position, max cost per order, max orders per day, max daily loss (then auto-halt), trading window, market data not older than N seconds. Closing sells skip the window, count and loss limits so a limit can never trap a position.

## Weekly login renewal (Schwab refresh token lasts 7 days)
- You get a notification and a ledger line about 24h before expiry.
- Run `./reauth`, log in at Schwab in the browser, paste the final address when asked. Done.
- If it expires first: the system sets the switch OFF, cancels open orders, notifies you, and logs why. Run `./reauth`, then `./arm` again.

## If something looks wrong
`./halt` first. Then `./status`. Then check open orders and positions in Schwab directly.
