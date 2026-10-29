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

## Placeholder decisions (config/paper.json)
Chosen so a first manual paper session can run. None is tuned, tested, or recommended.
- Few trades: one open position, at most two entries per session, one contract per trade.
- Rule A: entry window 09:45 to 14:30 ET, displayed size at least 5 on both sides, ask between
  $1.00 and $3.00. The band exists because friction dominates cheap premiums.
- Hard max loss = premium cap $300 per trade (long option, only guaranteed maximum).
  Soft stop at 50% net loss, which can be gapped through.
- Daily loss limit $300. Quote freshness 90 s for hand-typed quotes. Spread limit $0.05 absolute.
  Latest exit 15:30 ET. Fees $0.65 per contract per side, unverified.

## Unresolved decisions
- Market data provider and its licensing, latency, and timestamp semantics. Manual CSV is the
  only source now, and it cannot support more than a quote per contract every 30 to 60 s.
- Whether Rule A stays, and what any directional rule would be. Rule A has no view.
- Quote freshness must tighten to seconds if a real feed is connected.
- Spread limit unit: absolute dollars kept for now; percent of mid rejected for phase one.
- Slippage model: fixed one tick beyond the touch; size-aware fills are not modeled.
- Fee schedule: verify broker per-contract fee; regulatory fees on sells are not modeled.
- Holiday and early-close handling. None exists; `latest_exit_time` is wrong on 13:00 ET closes.
- Partial fills: entries are all-or-nothing by the size check; an exit that cannot fill in full
  stays pending and retries next tick.
- Evaluation metric for the 15% hypothesis: per-trade hit rate, expectancy, or distribution.
  One manual session cannot answer it; it only exercises the pipeline on real numbers.
