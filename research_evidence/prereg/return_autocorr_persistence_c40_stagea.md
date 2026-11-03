# Candidate 40 / return-autocorrelation persistence long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-40 result  
Key: `return_autocorr_persistence_long_1h`; timeframe: 1h; long only

## Duplicate-search evidence

Before freezing, the current tree and every Git branch, remote, tag and history were searched for return
autocorrelation, serial correlation, return persistence, lag-1 dependence and equivalent rolling-correlation signal
code. No tested strategy, preregistration, packet or result matched. The historical hits were unrelated BTC-to-alt
lead/lag wording. Existing momentum and trend cells use level ordering or cumulative returns, not measured serial
dependence of consecutive returns.

## Hypothesis

Positive lag-1 return autocorrelation identifies a short-lived state in which a fresh upside impulse is more likely to
continue than reverse. At a completed 1h bar, enter long when the rolling 24-observation correlation between returns and
their one-bar lags is at least 0.15, the close-to-close move is at least 0.35 ATR, close is above EMA20, EMA50 is above
its value eight bars ago, and quote volume is at least its prior 48-bar median. A 2 ATR stop must be at least five times
modeled round-trip fee+slippage. Fill at the next open.

## Frozen execution and costs

- Parameters: 24-return autocorrelation, 0.15 minimum, 0.35 ATR impulse, eight-bar EMA50 trend lag, 1.0x prior 48-bar volume.
- Management: 2 ATR stop, 2R target, 12-bar cap, earlier exit when autocorrelation is non-positive; re-entry lock.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified funding using causal 1h marks.
- One long cell only; no grid, short inversion or management diagnostic. Default-OFF; no PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if gross <=0, mean net R <=0, PF <=1.05,
<100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC regime. Inspect every gate before stopping. Per the
Candidate-40 contract, no folds run; holdout remains sealed regardless of the development outcome. No variants or rescue.

ROADMAP FIT: structurally distinct frequent statistical-dependence scout after candidate 39.  
DEFERRED: short cell, lookback/threshold alternatives, folds, holdout, Stage B, PAPER/testnet, runtime and mainnet.
