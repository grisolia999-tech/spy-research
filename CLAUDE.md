# spy-research

Research, paper-trading and (owner-armed) live execution for SPY 0DTE options. Long calls and long puts only.

## Scope
- Hypothesis under evaluation: a 15% return on option entry premium, net of fees and slippage. A hypothesis to test, never a promised or expected outcome.
- Two execution paths behind one broker interface: `paper` (simulated, buy at ask / sell at bid plus slippage and fees, never midpoint) and `live` (Schwab Trader API). Observed quotes, simulated fills and broker fills are distinct.
- Paper results do not establish live profitability. Say so whenever results are reported.

## The switch (hard rules)
- `config/switch.json` is `off | paper | live`. It is written only by the owner through `./arm` and `./halt`.
- **An agent never edits `config/switch.json`, never runs `./arm paper|live`, and never places, cancels-on-purpose or modifies a live order on its own initiative.** Agents build and test; the owner arms.
- Default state after any start, crash or deploy is `off`. `live` expires at 16:00 New York time.
- Risk limits (`config/limits.json`) are enforced before every order in every mode. Live requires `owner_reviewed: true`, which only the owner sets.
- Trade eligibility stays blocked until every required risk parameter is configured (`src/risk/checks.py:REQUIRED_PARAMETERS`).

## Other hard rules
- Do not invent prices, market data, credentials, API capabilities or profitable strategies. Mark unverified API details UNVERIFIED.
- Secrets live in the Mac Keychain (`schwab-api`, `anthropic-api`). Never in the repo, `env.sh`, logs or chat.
- AI stays outside the per-tick decision loop. Strategy rules are deterministic Python.
- Nothing billable (API calls beyond the monthly credit, paid datasets) without telling the owner first.
- One bounded task at a time. No autonomous agent teams, repeated repo-wide scans or large parameter sweeps.

## Responses
Concise. Lead with the outcome. Report created or changed files, unresolved requirements, and the next smallest task.

## Compute limits
- Python 3.11+, standard library only unless a dependency is explicitly approved.
- Tests: `python3 -m unittest discover -t . -s tests` must pass before anything is armed. Keep the suite under a few seconds.
- No backtests over large datasets or grid searches inside this repository.

## Layout
- `src/data` quotes, bars, Schwab quote adapter | `src/signals` ORB and rule A/exit rules | `src/risk` eligibility checks
- `src/paper` fee/slippage/fill simulation | `src/broker` interface, paper broker, Schwab Trader broker
- `src/control` switch, limits, gate, ledger, arm/halt/status | `src/engine` loop, bars, login watch, notifications
- `src/schwabapi` OAuth and `./reauth` | `src/vault` Keychain access | `src/journal` alerts and decision records
- `config/paper.json` strategy and paper settings (ORB rules explicit here) | `config/limits.json` risk limits
- `logs/trades.jsonl` append-only ledger (gitignored) | `RUNBOOK.md` operating guide | `docs/` research and reports
