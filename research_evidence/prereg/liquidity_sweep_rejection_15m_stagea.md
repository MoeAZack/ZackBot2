# LSR-15 Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any LSR-15 market-data result  
Key: `liquidity_sweep_rejection_15m`; timeframe: 15m; long and short judged separately

## Hypothesis and independence

An anomalous-volume candle that sweeps stops beyond a well-observed rolling edge but leaves a disproportionately long
wick and closes back inside may identify a short-lived liquidity rejection. LSR-15 enters only at the following open and
uses the actual sweep extreme to define risk.

This is not SC-FAILBRK: that rule requires a previous candle close outside an older range and a later close back inside
over a multi-bar window. LSR-15 requires the decision candle itself to wick outside while its close remains inside; a
close-outside event is not an entry. It is also distinct from SC-RANGE edge location, VWAP, compression/retest, ADX
pullbacks, RSI reclaim, Fibonacci, structure, BTC residual, cross-sectional/funding families, DCA and opening-range hold.

## Frozen causal mechanics

- Reference edge: high/low of the preceding 24 completed candles, excluding the decision candle.
- Long: decision low sweeps at least 0.25 ATR below the prior low, then closes above that low but below range midpoint.
  Short is mirrored above the prior high.
- Rejection quality: directional wick >= 0.75 ATR, >= 1.50 times real body, and close location recovers into the upper
  35% of the candle for long (lower 35% for short).
- Volume anomaly: positive decision volume >= 2.0 times median volume of the preceding 20 candles.
- Dynamic long stop distance is close minus sweep low plus 0.10 ATR; short mirrors. It is floored at 0.75 ATR and must
  be at least five times round-trip cost. Stage A charges 5 bp taker fee plus 2 bp adverse slippage on each fill and
  shared signed funding.
- Fill is next open or later. Management uses the dynamic stop distance (`stop_atr=1`), half at +0.8R with breakeven,
  +1.5R final target, range-mid/invalidating-close exit, 12-bar cap and strict 16-candle same-side cooldown.
- Regime neutral, one frozen primary, no threshold or symbol selection after results.

## Stage-A chronology and gates

CORE-10 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side-level reject: fewer than 60 closes,
mean net R <= 0, PF <= 1.05, or non-positive pre-cost gross expectancy. Only a development survivor runs unchanged:

1. `2025-04-01..2025-09-30`
2. `2025-10-01..2026-03-31`
3. `2026-04-01..2026-09-28` (exclusive)

Final Stage-A gate per side: aggregate mean net R > 0, PF > 1.05, >= 120 closes over >= eight symbols, at least two of
three positive folds, median >= eight closes/month, max drawdown <= 15R, and no symbol > 35% of absolute contribution.
The sealed post-2026-09-28 holdout remains untouched. Sides cannot pool or rescue one another.

Multiplicity: two new cells. A failure is recorded and parked without tuning.

ROADMAP FIT: cost-aware 15-minute conveyor and a distinct liquidity-rejection return source.  
DEFERRED: Stage B, holdout, PAPER/testnet, runtime settings, mainnet and non-Binance venues.

## Run command

```powershell
py -3.14 tools/research/run_breadth_candidate.py --cache <15M_CACHE> --regime-cache <4H_CACHE> --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-10 --key liquidity_sweep_rejection_15m --side long --mgmt defaults --out <OUT>\lsr15-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to the frozen folds.
