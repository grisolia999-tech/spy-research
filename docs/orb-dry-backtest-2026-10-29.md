# ORB dry backtest report (2026-10-29)

**Read this first: this report cannot show whether ORB is profitable.** Paper and historical results never establish live profitability.

## What was run
`scripts/orb_dry_report.py` applied the ORB rule in `config/paper.json -> orb` (opening range 09:30-09:35, trigger window 09:36-11:00, first completed close beyond the range) to the two SPY exports in the project. It uses the underlying only, one fixed parameter set, no sweeps. No option prices are used or invented.

## Data available
| file | bars | sessions with a complete 09:30-09:35 range |
|---|---|---|
| BATS_SPY 1 (1-minute) | 742 bars, 2026-07-08 only | 1 |
| BATS_SPY 5 (5-minute) | 397 bars, 2026-07-06 (evening only), 07-07, 07-08 | 2 |

That is **2 distinct sessions** (07-07, 07-08; 07-08 appears in both files and gave the same direction). Two sessions carry no statistical weight.

## Result (SPY dollars, from the trigger bar's close until 15:30)
| session | range | signal | best move in favour | worst move against | 15:30 move |
|---|---|---|---|---|---|
| 07-07 (5m) | 749.40-750.95 | PUT at 09:55 @ 748.91 | +3.70 | -0.43 | +2.21 |
| 07-08 (1m) | 742.67-744.07 | CALL at 09:41 @ 744.59 | +1.49 | -5.08 | +0.60 |
| 07-08 (5m) | 742.67-744.07 | CALL at 09:45 @ 744.32 | +1.76 | -4.81 | +0.87 |

## What this does and does not tell you
- It confirms the rule fires, picks a direction and trigger time deterministically, and the code is tested.
- Best and worst excursions are not achievable fills, and the report does not say which came first. The order decides whether a 15% target or a 50% soft stop hits first, so option outcomes cannot be inferred.
- Turning SPY moves into option returns needs real option bid/ask history, which this repo does not have. Earlier research found that buying 0DTE options is negative expectancy after costs in the datasets reviewed.

## Decisions for you
1. **More underlying history (free, read-only):** Schwab's price-history endpoint can supply more intraday SPY bars once `./reauth` has stored a login. That would give a more meaningful count of sessions but still no option prices.
2. **Option-level history is not available from Schwab for expired contracts.** A paid historical options dataset would be billable, so I will not buy or call one without your approval.
3. **Otherwise** the next evidence comes from running `paper` mode with the live Schwab feed and logging real executable quotes, one session at a time.
