# ORH-15 Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any ORH-15 market-data result  
Key: `utc_opening_range_hold_15m`; timeframe: 15m; long and short are separate regime-neutral cells

## Hypothesis and independence

The first break of the UTC session's opening range may continue when the next completed candle holds outside that fixed
range and participation remains adequate. Waiting for a separate hold candle is intended to avoid paying for a one-bar
false break while retaining more opportunities than strict trend/regime candidates.

ORH-15 is not rolling Donchian/squeeze release, volatility compression/retest, failed-breakout mean reversion, VWAP,
ADX/DMI pullback, RSI reclaim, Fibonacci, swing structure, BTC residual, cross-sectional rank, funding/OI, DCA, or any
existing runtime rule. It uses a fixed four-candle UTC opening range and has no market-regime admission filter.

## Frozen causal mechanics

- Opening range is the high/low of exactly the first four completed 15m candles of each UTC day (`00:00..00:45`). It is
  unavailable until all four candles close and never changes afterward.
- The breakout candle `t-1` must be the first close crossing above the opening high for long or below the opening low
  for short. Its volume must be >= 1.20 times the median of the 20 candles ending before the breakout.
- Decision candle `t` must remain outside the range. Long close > opening high and low no worse than 0.15 ATR below it;
  short is mirrored. Decision volume must be >= 0.75 times the preceding-20 median.
- The 1.0 ATR stop must be at least five times round-trip cost. Stage A applies 5 bp taker fee plus 2 bp adverse
  slippage on each fill and the shared funding accounting.
- Fill occurs no earlier than the next open. Management is 1.0 ATR stop, half at +0.8R with breakeven, +1.8R final
  target, 12-bar cap, close-back-inside exit and an eight-candle same-side lock.
- Long and short use `when=any`; neither is conditioned on the BTC regime. One frozen primary, no threshold grid.

## Stage-A chronology and gates

CORE-10 development is `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side-level reject: fewer than 60 closes,
mean net R <= 0, PF <= 1.05, or non-positive pre-cost gross expectancy. Only a development survivor runs unchanged:

1. `2025-04-01..2025-09-30`
2. `2025-10-01..2026-03-31`
3. `2026-04-01..2026-09-28` (exclusive)

Final Stage-A gate per side: aggregate mean net R > 0, PF > 1.05, >= 120 closes over >= eight symbols, at least two of
three positive folds, median >= eight closes/month, max drawdown <= 15R, and no symbol > 35% of absolute contribution.
The sealed post-2026-09-28 holdout stays untouched. Sides cannot pool or rescue one another.

Multiplicity: two new cells. Failure is recorded and parked without post-result adjustment.

ROADMAP FIT: continuous cost-aware 15-minute conveyor with a higher-frequency regime-neutral hypothesis.  
DEFERRED: Stage B, holdout, PAPER/testnet, runtime settings, mainnet and non-Binance venues.

## Run command

```powershell
py -3.14 tools/research/run_breadth_candidate.py --cache <15M_CACHE> --regime-cache <4H_CACHE> --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-10 --key utc_opening_range_hold_15m --side long --mgmt defaults --out <OUT>\orh15-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to the frozen folds.
