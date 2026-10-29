---
inclusion: always
---

# spy-research project guidance

SPY 0DTE options research and paper trading. Long calls and long puts only. Python 3.11+,
standard library only unless a dependency is explicitly approved.

## Treat as fixed
- A 15% return on option entry premium is a hypothesis to evaluate, not a target to assume.
- Paper mode only in this phase. No broker integration, no live orders, no credentials.
- Returns come from simulated executable fills including fees and slippage. Never midpoint.
- Observed quotes (`src/data`) and simulated fills (`src/paper`) are separate types; never
  mix them or label one as the other.
- Trade eligibility is blocked until entry rules, max loss per trade, daily loss limit, quote
  freshness limit, spread limit, and latest exit time are all configured.
- Paper results never establish live profitability.

## Working style
- Concise answers. One bounded task at a time.
- No autonomous agent teams, repeated repository-wide scans, or large parameter searches.
- Do not invent prices, data, API capabilities, or profitable strategies.
- Keep AI outside the per-tick decision loop; rules in `src/signals` are deterministic.
- Alerts use the compact format: `TIME | CONTRACT | STATUS | RULE | DATA AGE | SPREAD | RISK CHECK`.

## Verify before finishing
- `python -m unittest discover -t . -s tests` passes.
- Report created files, unresolved requirements, and the next smallest implementation task.
