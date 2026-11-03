# Candidate 32 / inside-bar breakout long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-32 result  
Key: `inside_bar_breakout_long_1h`; timeframe: 1h; long only

## Duplicate-search evidence

Before freezing, the current tree and every Git ref/history were searched for `inside bar`, `inside_bar`, `mother bar`,
`narrow range`, `range contraction`, and equivalent close-breakout patterns. No strategy, preregistration, run packet or
result used an inside/mother-bar sequence. Existing volatility-compression, daily-channel breakout, close-location,
liquidity-sweep and range-reversion families were inspected and are separate trials.

## Hypothesis

An expanded 1h mother bar followed by a fully contained contraction can preserve directional pressure. At a completed
breakout bar, enter long only when the mother range two bars ago was at least 1.25 ATR, the intervening bar stayed inside
that range and contracted to at most 70% of it, the current close is above the mother high and EMA50, and current quote
volume is at least its prior 48-bar median. A 2 ATR stop must be at least five times modeled round-trip fee+slippage.
Fill at the next open; all pattern inputs are completed bars.

## Frozen execution and costs

- Parameters: 1.25 ATR mother, 70% inside-range cap, 48-bar median quote volume, 1.0x participation, 5x cost admission.
- Management: 2 ATR stop, 2R target, 16-bar cap, earlier exit below EMA20; re-entry lock.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified funding using causal 1h marks.
- One long cell only; no grid, short inversion or management diagnostic. Default-OFF; no PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if gross <=0, mean net R <=0, PF <=1.05,
<100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC regime. Inspect every gate before folds. A survivor
runs unchanged on three chronological folds. Final: mean R >0, PF >1.05, >=200 closes/8 symbols, >=2/3 positive folds,
median >=8 closes/month, DD <=15R, neither regime mean below -0.05R, no symbol >35% absolute contribution. Holdout sealed.

ROADMAP FIT: next orthogonal frequent-strategy scout after candidate 31, with explicit all-ref duplicate search.  
DEFERRED: short cell, alternative inside-bar definitions, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
