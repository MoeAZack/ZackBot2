# Candidate 36 / MFI oversold recovery long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-36 result  
Key: `mfi_oversold_recovery_long_1h`; timeframe: 1h; long only

## Duplicate-search evidence

Before freezing, the current tree and every Git ref/history were searched for Money Flow Index, `MFI`, positive/negative
money flow, and typical-price money-flow logic. No strategy, preregistration, packet or result matched. Generic
typical-price-times-volume hits implement VWAP and do not form volume-signed flow ratios. Existing RSI recovery is
price-only and therefore a distinct family.

## Hypothesis

Volume-weighted selling pressure may mean-revert when 14-bar Money Flow Index recovers from oversold. At a completed 1h
bar, compute standard MFI from typical price times base volume, assigning flow by the sign of typical-price change. Enter
long only when MFI crosses above 20 from at or below 20, the candle closes above its open, and quote volume is at least
80% of its prior 48-bar median. A 2 ATR stop must be at least five times modeled round-trip fee+slippage. Fill next open.

## Frozen execution and costs

- Parameters: MFI14, oversold recovery 20, MFI exit 60, 0.8x prior 48-bar quote-volume floor, 5x cost admission.
- Management: 2 ATR stop, 1.5R target, 12-bar cap, earlier exit at MFI60 or EMA20; re-entry lock.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified funding using causal 1h marks.
- One long cell only; no grid, short inversion or management diagnostic. Default-OFF; no PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if gross <=0, mean net R <=0, PF <=1.05,
<100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC regime. Inspect every gate before folds. A survivor
runs unchanged on three chronological folds. Final: mean R >0, PF >1.05, >=200 closes/8 symbols, >=2/3 positive folds,
median >=8 closes/month, DD <=15R, neither regime mean below -0.05R, no symbol >35% absolute contribution. Holdout sealed.

ROADMAP FIT: distinct frequent volume-weighted mean-reversion scout after candidate 35.  
DEFERRED: short cell, MFI/window alternatives, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
