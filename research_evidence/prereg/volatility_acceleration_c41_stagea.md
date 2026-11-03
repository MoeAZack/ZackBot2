# Candidate 41 / volatility acceleration long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-41 result  
Key: `volatility_acceleration_long_1h`; timeframe: 1h; long only

## Duplicate-search evidence

Before freezing, the current tree and every Git branch, remote, tag and history were searched for ATR ratio, fast/slow
ATR, volatility acceleration, range term structure and volatility-regime crossover signal code. No tested strategy,
preregistration, packet or result matched. Existing range expansion, squeeze and ATR-shock cells use single-bar extremes,
price compression or reversal; none trades a short/long true-range ratio continuation state.

## Frozen hypothesis, execution and costs

Enter long at the next open when the completed bar's six-bar mean true range is at least 1.20 times the prior 48-bar
mean true range, its bullish body is at least 25% of its range, close is above EMA20, EMA50 exceeds its value eight bars
ago, and quote volume is at least its prior 48-bar median. Require a 2 ATR stop to be at least five times modeled
round-trip costs. Exit if the ratio falls below 1.0, at 2 ATR stop, 2R target or 12 bars. Re-entry lock. Costs are 5 bp
taker plus 2 bp adverse slippage per fill and checksum-verified actual funding. One long cell; default-OFF.

## Stage-A gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if gross <=0, mean net R <=0, PF <=1.05,
<100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC regime. Inspect all gates; no folds, holdout, rescue,
PAPER/testnet or runtime action regardless of outcome.

ROADMAP FIT: structurally distinct frequent volatility-term-structure scout after candidate 40.  
DEFERRED: short cell, threshold variants, folds, holdout, Stage B, PAPER/testnet, runtime and mainnet.
