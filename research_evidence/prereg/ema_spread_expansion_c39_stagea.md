# Candidate 39 / EMA spread expansion long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-39 result  
Key: `ema_spread_expansion_long_1h`; timeframe: 1h; long only

## Duplicate-search evidence

Before freezing, the current tree and every Git ref/history were searched for EMA or moving-average compression,
EMA-spread expansion/breakout, moving-average spread, and equivalent normalized-spread signal code. No strategy,
preregistration, packet or result matched. Existing EMA crossover/momentum cells test ordering/crosses without a recent
ATR-normalized convergence condition. Bollinger/squeeze cells compress price volatility, not the EMA20/EMA50 spread.

## Hypothesis

Directional separation of intermediate moving averages after recent convergence can identify emerging trends without
chasing an established wide spread. At a completed 1h bar, enter long when the minimum absolute EMA20–EMA50 spread over
the preceding 12 bars was at most 0.25 ATR, the current positive spread crosses above 0.50 ATR, EMA50 is above its value
eight bars ago, and close is above EMA20. Quote volume must be at least its prior 48-bar median. A 2 ATR stop must be at
least five times modeled round-trip fee+slippage. Fill at the next open.

## Frozen execution and costs

- Parameters: 12-bar convergence memory, 0.25 ATR compression, 0.50 ATR expansion, 1.0x prior 48-bar volume, 5x cost admission.
- Management: 2 ATR stop, 2R target, 24-bar cap, earlier exit when EMA20–EMA50 spread is non-positive; re-entry lock.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified funding using causal 1h marks.
- One long cell only; no grid, short inversion or management diagnostic. Default-OFF; no PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if gross <=0, mean net R <=0, PF <=1.05,
<100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC regime. Inspect every gate before folds. A survivor
runs unchanged on three chronological folds. Final: mean R >0, PF >1.05, >=200 closes/8 symbols, >=2/3 positive folds,
median >=8 closes/month, DD <=15R, neither regime mean below -0.05R, no symbol >35% absolute contribution. Holdout sealed.

ROADMAP FIT: structurally distinct frequent trend-emergence scout after candidate 38.  
DEFERRED: short cell, spread/window alternatives, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
