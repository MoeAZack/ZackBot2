# Candidate 33 / RSI lower-low divergence long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-33 result  
Key: `rsi_lower_low_divergence_long_1h`; timeframe: 1h; long only

## Duplicate-search evidence

Before freezing, the current tree and every Git ref/history were searched for `RSI divergence`, `bullish divergence`,
`bearish divergence`, `momentum divergence`, price lower-low/RSI higher-low combinations, and equivalent strategy code.
No strategy, preregistration, packet or result matched. The existing `pullback_rsi` family uses a fixed RSI recovery
threshold inside an EMA trend and does not compare a fresh price low with a non-confirming oscillator low.

## Hypothesis

A fresh 1h closing low can mean-revert when 14-period RSI does not confirm the new low. At a completed bar, enter long
when close is below the minimum close from the prior separated 44-bar comparison window, RSI14 is at least eight points
above that window's minimum RSI, the candle closes above its open, and quote volume is at least 80% of its prior 48-bar
median. The four-bar separation prevents the current low cluster from defining its own reference. A 2 ATR stop must be
at least five times modeled round-trip fee+slippage. Fill at the next open.

## Frozen execution and costs

- Parameters: 48-bar window, four-bar separation, eight RSI-point divergence, 0.8x quote-volume floor, 5x cost admission.
- Management: 2 ATR stop, 1.5R target, 12-bar cap, earlier exit at EMA20; re-entry lock.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified funding using causal 1h marks.
- One long cell only; no grid, short inversion, pivot tuning or management diagnostic. Default-OFF; no PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if gross <=0, mean net R <=0, PF <=1.05,
<100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC regime. Inspect every gate before folds. A survivor
runs unchanged on three chronological folds. Final: mean R >0, PF >1.05, >=200 closes/8 symbols, >=2/3 positive folds,
median >=8 closes/month, DD <=15R, neither regime mean below -0.05R, no symbol >35% absolute contribution. Holdout sealed.

ROADMAP FIT: distinct frequent mean-reversion scout after candidate 32, with explicit all-ref duplicate search.  
DEFERRED: short cell, alternate pivots/windows, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
