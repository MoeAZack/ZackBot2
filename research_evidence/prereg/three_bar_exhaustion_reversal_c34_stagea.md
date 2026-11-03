# Candidate 34 / three-bar exhaustion reversal long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-34 result  
Key: `three_bar_exhaustion_reversal_long_1h`; timeframe: 1h; long only

## Duplicate-search evidence

Before freezing, the current tree and every Git ref/history were searched for consecutive down/red/bear bars, three-bar
reversal or exhaustion, losing/down streak strategies, multi-bar exhaustion, and equivalent signal code. No strategy,
preregistration, packet or result matched. Historical text hits were operational consecutive-failure checks and reporting
of losing streaks. ATR-shock and transient-jump candidates require one exceptional bar; this family requires distributed
three-bar pressure and a separate reversal candle.

## Hypothesis

Downside pressure distributed across three consecutive descending red 1h bars can become exhausted without requiring a
single shock bar. At a completed reversal bar, enter long when the preceding three candles each closed below their open
and below the following older close, their cumulative open-to-close decline was at least 1.5 ATR, and the current bullish
candle closes above the immediately prior high. Quote volume must be at least 80% of its prior 48-bar median. A 2 ATR stop
must be at least five times modeled round-trip fee+slippage. Fill at the next open.

## Frozen execution and costs

- Parameters: three-bar red/descending streak, 1.5 ATR cumulative drop, 0.8x prior 48-bar quote-volume floor, 5x cost admission.
- Management: 2 ATR stop, 1.5R target, 12-bar cap, earlier exit at EMA20; re-entry lock.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified funding using causal 1h marks.
- One long cell only; no grid, short inversion or management diagnostic. Default-OFF; no PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if gross <=0, mean net R <=0, PF <=1.05,
<100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC regime. Inspect every gate before folds. A survivor
runs unchanged on three chronological folds. Final: mean R >0, PF >1.05, >=200 closes/8 symbols, >=2/3 positive folds,
median >=8 closes/month, DD <=15R, neither regime mean below -0.05R, no symbol >35% absolute contribution. Holdout sealed.

ROADMAP FIT: distinct frequent mean-reversion scout after candidate 33, with explicit all-ref duplicate search.  
DEFERRED: short cell, streak/drop alternatives, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
