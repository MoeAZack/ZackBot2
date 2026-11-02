# MBP-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any MBP-1H market-data result  
Key: `mtf_breakout_pullback_1h`; timeframe: 1h decisions with completed 4h symbol context

## Hypothesis and independence

A completed 4h range breakout may create a level that subsequently supports continuation. MBP-1H does not chase the
breakout: it waits for a separate 1h retracement that holds the broken edge and then a later 1h continuation candle with
relative-volume confirmation. This should offer larger gross moves and lower fee turnover than the rejected 15m batch.

This is not the runtime 4h breakout sleeve, which enters the breakout itself; not Fibonacci, 15m compression/retest,
EDGE-02 structure/RSI/squeeze/residual, EDGE-03 relative-strength/funding, XAV-1H acceleration, VWAP, ADX pullback,
opening range, liquidity sweep, DCA or any existing runtime entry rule.

## Frozen causal mechanics

- Aggregate only groups of four complete 1h candles into 4h OHLC. An incomplete current 4h block is unavailable.
- Long breakout: completed 4h close crosses above the high of the preceding 20 completed 4h candles while 4h EMA20 >
  EMA50. Short mirrors below the preceding 20-bar low with EMA20 < EMA50.
- The broken edge arms that symbol/side for 16 hourly candles beginning only after the 4h breakout close is available.
- Pullback at `t-1`: long low touches within 0.50 ATR above the armed high and closes at/above the level; short mirrors.
- Confirmation at closed `t`: long closes above the `t-1` high with bullish body >= 0.25 ATR; short mirrors. Local 1h
  EMA20/EMA50 trend must agree. Decision volume >= 1.25 times median of the preceding 20 completed 1h candles.
- The 1.5 ATR stop must be >= five times round-trip cost. Stage A charges 5 bp taker fee plus 2 bp adverse slippage on
  each fill plus shared signed funding. Entry is next open.
- Management: 1.5 ATR stop, half at +1R with breakeven, +2.5R final target, 16-hour cap, broken-level/local-trend exit
  and same-decision-bar re-entry lock. One frozen primary; no parameter grid.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side-level reject: mean net R <= 0,
PF <= 1.05, non-positive pre-cost gross expectancy, fewer than 100 closes, or fewer than eight symbols with closes.
Only a development survivor runs unchanged:

1. `2025-04-01..2025-09-30`
2. `2025-10-01..2026-03-31`
3. `2026-04-01..2026-09-28` (exclusive)

Final Stage-A gate per side: aggregate mean net R > 0, PF > 1.05, >= 200 total closes over >= eight symbols, at least
two of three positive folds, median >= eight closes/month, max drawdown <= 15R, and no symbol > 35% of absolute
contribution. The post-2026-09-28 holdout stays sealed. Sides cannot pool or rescue one another.

Multiplicity: two new cells. Any failure is recorded and parked without threshold, symbol or management rescue.

ROADMAP FIT: lower-turnover, larger-move candidate after five cost-dominated 15m failures.  
DEFERRED: Stage B, holdout, PAPER/testnet, runtime settings, mainnet and non-Binance venues.

## Run command

```powershell
py -3.14 tools/research/run_mtf_pullback_candidate.py --cache <1H_CACHE> --regime-cache <4H_CACHE> --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long --out <OUT>\mbp1h-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to the frozen folds.
