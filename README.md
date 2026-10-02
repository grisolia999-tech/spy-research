# spy-research

Lean SPY 0DTE options research and paper-trading scaffold. Long calls and long puts only.
The 15% return-on-premium idea is a hypothesis under evaluation, not an expected result.
Paper results never establish live profitability.

## Setup
Python 3.11+, no third-party dependencies.

```
cp config/paper.example.json config/paper.json   # then set every null value
python -m unittest discover -t . -s tests
```

## What exists
- `src/data` observed quote interface and labeled synthetic sample data
- `src/signals` deterministic entry/exit rule interfaces (`NoEntryRule` is the only entry rule)
- `src/risk` eligibility checks; blocked until all required parameters are set
- `src/paper` simulated orders, fills at the touch plus slippage, fees, round-trip returns, and a single-tick runner (`runner.run_tick`)
- `src/journal` compact alerts, trade and decision records, JSON-lines journal
- `docs/requirements.md` data needs, workflow, unresolved decisions

## Current limitations
- No market data provider. All quotes are synthetic and labeled `SAMPLE_SYNTHETIC`.
- No entry strategy. Eligibility is blocked until `entry_rules` and five risk limits are set.
- No live broker, no order routing, no credentials. `run_tick` evaluates one tick; there is no scheduler or loop.
- Fills are simulated at ask/bid plus fixed tick slippage; no queue or size-aware modeling.
- Fee values in the example config are placeholders, not a broker schedule.
- No holiday calendar; session timezone defaults to America/New_York.
