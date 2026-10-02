# spy-research

Lean research and paper-trading scaffold for SPY 0DTE options. Long calls and long puts only.

## Scope
- Hypothesis under evaluation: a 15% return on option entry premium, net of simulated fees and
  slippage. This is a hypothesis to test, never a promised or expected outcome.
- Paper mode only. No live broker integration, no order routing, no credentials.
- Returns are computed from simulated executable fills (buy at ask, sell at bid, plus slippage
  and fees). Never assume midpoint fills. Observed quotes and simulated fills are distinct types.
- Paper results do not establish live profitability. Say so whenever results are reported.

## Hard rules
- Do not invent prices, market data, credentials, API capabilities, or profitable strategies.
- Trade eligibility stays blocked until every required risk parameter is configured
  (see `src/risk/checks.py:REQUIRED_PARAMETERS`).
- AI stays outside the per-tick decision loop. Strategy rules are deterministic Python.
- One bounded task at a time. No autonomous agent teams, no repeated repository-wide scans,
  no large parameter searches or optimization sweeps.

## Responses
- Concise. Lead with the outcome. Report created or changed files, unresolved requirements,
  and the next smallest implementation task.

## Compute limits
- Python 3.11+, standard library only unless a dependency is explicitly approved.
- Tests: `python -m unittest discover -t . -s tests`. Keep the suite under a few seconds.
- Do not run backtests over large datasets or grid searches inside this repository.

## Layout
- `src/data` quote interface and labeled sample data
- `src/signals` deterministic strategy rule interfaces
- `src/risk` eligibility and session-loss checks
- `src/paper` simulated orders, fills, fees, slippage
- `src/journal` alerts, trades, decision reasons
- `config/paper.example.json` paper configuration template
- `docs/requirements.md` data needs, workflow, unresolved decisions
