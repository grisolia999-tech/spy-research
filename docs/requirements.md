# Requirements

## Goal
Evaluate, in paper mode, whether long SPY 0DTE calls and puts can reach a 15% return on entry
premium net of simulated fees and slippage. This is a hypothesis. Nothing in this repository
assumes it is true, and paper results never establish live profitability.

## Data needs
| Need | Status |
|---|---|
| Timestamped SPY option quotes (bid, ask, sizes, observed time, source) | Interface defined in `src/data/quotes.py`; only labeled synthetic sample data exists |
| Underlying SPY price alongside each quote | Field present on `OptionQuote`; no live source |
| Quote source identity and clock source | Required on every quote; sample source is `SAMPLE_SYNTHETIC` |
| Historical 0DTE quote archive for replay | Not available; see unresolved decisions |
| Fee schedule (per contract, per order, regulatory) | Configurable in `config/*.json`; defaults are placeholders, not a broker's schedule |

No live or historical market data is included. Sample data is synthetic, clearly labeled,
and must never be used to judge strategy performance.

## Workflow
1. Copy `config/paper.example.json` to `config/paper.json` and set every required parameter.
2. A quote source delivers `OptionQuote` records (observed data).
3. Deterministic entry rules in `src/signals` emit a `Signal`. No AI in this loop.
4. `src/risk` runs eligibility checks: configuration complete, data present and fresh, spread
   within limit, position limits, session loss limit, time before latest exit.
5. Blocked signals produce an alert and a journaled decision reason. Nothing is traded.
6. Eligible signals become `PaperOrder`s; `src/paper` simulates fills at the touch plus
   slippage, applies fees, and records a `PaperFill` (simulated data).
7. Exit rules check profit target, max loss, and latest exit time against fresh quotes.
8. `src/journal` records alerts, trades, and decisions as JSON lines for later review.

## Unresolved decisions
- Market data provider and its licensing, latency, and timestamp semantics.
- Entry rule definition. None is proposed; `NoEntryRule` is the only implementation.
- Maximum loss per trade, daily loss limit, quote freshness limit, spread limit, and latest
  exit time. All unset in the example config and all block eligibility until set.
- Spread limit unit: absolute dollars, percent of mid, or both.
- Slippage model: fixed ticks beyond the touch is the placeholder; size-aware fills are not modeled.
- Fee schedule: per-contract and per-order values must come from the actual broker used for paper.
- Session timezone and holiday handling. Placeholder is `America/New_York`, no holiday calendar.
- Whether partial fills (order quantity above displayed size) are allowed or rejected.
- Evaluation metric for the 15% hypothesis: per-trade hit rate, expectancy, or distribution.
