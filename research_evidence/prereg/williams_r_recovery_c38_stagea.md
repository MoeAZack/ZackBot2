# Candidate 38 / Williams %R recovery long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-38 result  
Key: `williams_r_recovery_long_1h`; timeframe: 1h; long only

## Duplicate-search evidence

Before freezing, the current tree and every Git ref/history were searched for Williams %R, Williams R, percent-R,
rolling-range oscillators, and highest-high/lowest-low close-location formulas. No strategy, preregistration, packet or
result matched. RSI and MFI use smoothed directional returns or volume-weighted money flow; this family uses only close
location within the causal high/low range.

## Hypothesis

A close moving back from the bottom of its recent price range can mark short-horizon exhaustion. At a completed 1h bar,
compute standard Williams %R over the current and preceding 13 completed bars. Enter long when %R crosses above -80
after being at or below -80, the recovery candle is bullish, and quote volume is at least 80% of its prior 48-bar median.
A 2 ATR stop must be at least five times modeled round-trip fee+slippage. Fill at the next open.

## Frozen execution and costs

- Parameters: Williams %R(14), oversold recovery -80, exit -20, 0.8x prior 48-bar quote-volume floor, 5x cost admission.
- Management: 2 ATR stop, 1.5R target, 12-bar cap, earlier exit at %R -20 or EMA20; re-entry lock.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified funding using causal 1h marks.
- One long cell only; no grid, short inversion or management diagnostic. Default-OFF; no PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if gross <=0, mean net R <=0, PF <=1.05,
<100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC regime. Inspect every gate before folds. A survivor
runs unchanged on three chronological folds. Final: mean R >0, PF >1.05, >=200 closes/8 symbols, >=2/3 positive folds,
median >=8 closes/month, DD <=15R, neither regime mean below -0.05R, no symbol >35% absolute contribution. Holdout sealed.

ROADMAP FIT: distinct frequent price-location mean-reversion scout after candidate 37.  
DEFERRED: short cell, oscillator/window alternatives, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
