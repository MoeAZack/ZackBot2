# RAS-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any RAS-1H market-data result  
Key: `regime_adaptive_switch_1h`; timeframe: 1h entries, completed 4h symbol state

## Hypothesis and independence

Continuation and mean reversion should not be applied in the same state. RAS-1H uses only completed 4h candles to
choose a high-efficiency trend mechanism or a low-efficiency/low-volatility mean-reversion mechanism before evaluating a
1h signal. The switch and both sub-rules are one frozen family; modes cannot be selected after results.

This is distinct from EDGE-02/03, runtime EMA/breakout/range strategies, VWAP, Fibonacci, ADX pullback, compression,
opening range, liquidity sweep, XAV-1H acceleration and MBP-1H breakout-pullback. It ranks neither symbols nor returns.

## Frozen causal state and entries

- Aggregate only complete groups of four 1h candles into 4h OHLCV. An incomplete current block is unavailable.
- 4h directional efficiency over 12 bars is `abs(close-close[-12]) / sum(abs(diff(close)),12)`.
- 4h normalized ATR(14) is ranked causally within its trailing 120 completed 4h observations.
- Trend state: efficiency >= 0.40 and volatility percentile >= 0.35. Mean state: efficiency <= 0.25 and volatility
  percentile <= 0.65. Other/overlap states admit no entry; thresholds are fixed.
- Trend entry: in trend state, long is a first 1h close above the preceding six-candle high with EMA20 > EMA50; short
  mirrors. Mean entry: in mean state, prior close is >=1.50 ATR below EMA20 and the decision is a bullish prior-high
  reclaim no later than EMA20+0.25 ATR; short mirrors.
- Both modes require decision volume >= median of the preceding 20 candles.
- The 1.5 ATR stop must be >= five times round-trip cost. Stage A charges 5 bp taker fee plus 2 bp adverse slippage on
  every fill plus shared signed funding. Entry is next open.
- Management: 1.5 ATR stop, half at +1R with breakeven, +2R final target, 16-hour cap, mode-specific trend/mean exit and
  same-decision-bar re-entry lock. One frozen primary, no threshold grid.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side-level reject: mean net R <= 0,
PF <= 1.05, non-positive pre-cost gross expectancy, fewer than 100 closes, or fewer than eight symbols with closes.
Only a development survivor runs unchanged:

1. `2025-04-01..2025-09-30`
2. `2025-10-01..2026-03-31`
3. `2026-04-01..2026-09-28` (exclusive)

Final Stage-A gate per side: aggregate mean net R > 0, PF > 1.05, >= 200 total closes across >= eight symbols, at least
two of three positive folds, median >= eight closes/month, maximum drawdown <= 15R, and no symbol >35% of absolute
contribution. The sealed post-2026-09-28 holdout remains untouched. Sides and modes cannot pool to rescue failures.

Multiplicity: long and short are two new cells. Failure is recorded and parked without post-result tuning.

ROADMAP FIT: causal regime-adaptive 1h candidate after non-generalizing fixed-mechanism trials.  
DEFERRED: Stage B, holdout, PAPER/testnet, runtime settings, mainnet and non-Binance venues.

## Run command

```powershell
py -3.14 tools/research/run_regime_adaptive_candidate.py --cache <1H_CACHE> --regime-cache <4H_CACHE> --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long --out <OUT>\ras1h-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to the frozen folds.
