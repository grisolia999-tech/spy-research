# spy-research

Lean SPY 0DTE options research and paper-trading scaffold. Long calls and long puts only.
The 15% return-on-premium idea is a hypothesis under evaluation, not an expected result.
Paper results never establish live profitability.

## Setup
Python 3.11+, no third-party dependencies.

```
python -m unittest discover -t . -s tests
```

## Running a manual paper session
`config/paper.json` holds placeholder limits and Rule A (time window, displayed size, premium
band $1.00 to $3.00). Nothing in it is tuned or recommended. Each morning edit the `contracts`
list: same-day expiry and strikes near the SPY price you see. Then:

```
python run_paper.py --config config/paper.json --quotes quotes.csv
```

The script creates `quotes.csv` with a header. During the session type what you observe,
one row per quote, and save. Columns: `time,expiry,strike,right,bid,ask,bid_size,ask_size,underlying`
with `time` as `HH:MM:SS` in Eastern time. Example row:

```
10:31:00,2026-10-05,500,CALL,1.22,1.28,30,20,500.30
```

The terminal prints one alert line per evaluation and `journal-<date>.jsonl` records every
alert, decision, and simulated trade. Stop with Ctrl-C.

Why the premium band and few trades: round-trip friction (two fees, the spread, slippage) is
paid win or lose. On a $0.50 option a net 15% needs the bid to rise about 36%; on a $2.00
option about 20%. Entries are capped at two per session and one open position.

## What exists
- `src/data` observed quote interface, labeled synthetic sample data, manual CSV quote source
- `src/signals` deterministic entry/exit rules: `NoEntryRule` (default) and `LiquidityWindowEntry` (Rule A, a gate with no directional opinion)
- `src/risk` eligibility checks; blocked until all required parameters are set
- `src/paper` simulated orders, fills at the touch plus slippage, fees, round-trip returns, and a single-tick runner (`runner.run_tick`)
- `src/journal` compact alerts, trade and decision records, JSON-lines journal
- `docs/requirements.md` data needs, workflow, unresolved decisions

## Current limitations
- No market data provider. Quotes are synthetic (`SAMPLE_SYNTHETIC`) or hand-typed (`MANUAL_CSV`).
- Rule A is a liquidity gate, not a strategy. It has no view on direction.
- No live broker, no order routing, no credentials. `run_paper.py` loops on the machine clock.
- The soft stop can be gapped through; the hard maximum loss is the premium paid.
- Regulatory fees on option sells are not modeled. Verify the fee placeholders against your broker.
- Fills are simulated at ask/bid plus fixed tick slippage; no queue or size-aware modeling.
- Fee values in the example config are placeholders, not a broker schedule.
- No holiday calendar; session timezone defaults to America/New_York.
