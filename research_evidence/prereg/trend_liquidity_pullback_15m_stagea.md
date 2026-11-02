# TLP-15 Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any TLP-15 market-data result  
Key: `trend_liquidity_pullback_15m`; timeframe: 15m; sides judged independently

## Hypothesis and independence

A strong directional trend can absorb a brief countertrend pullback and resume when participation returns. TLP-15 tests
that continuation only when closed-bar ADX/DMI strength, multi-bar price behavior, candle-location pressure and
notional-volume confirmation agree.

It is not EDGE-02 RSI2 reclaim (no oscillator exhaustion or threshold cross), the existing EMA9-touch fast rule (two-bar
countertrend sequence plus ADX/DMI and liquidity confirmation), VWAP continuation/reversion, compression/retest,
Fibonacci, structure breakout, BTC residual, EDGE-03 cross-sectional/funding, DCA, or a first-impulse chase. All previous
trials remain counted.

## Frozen causal mechanics

- Trend: long requires EMA20 > EMA50 > EMA200, EMA50 above its value eight bars earlier, ADX(14) >= 25 and above its
  value four bars earlier, and Wilder +DI >= 1.20 × -DI. Short is the exact mirror.
- Pullback: for long, close at `t-2` > close at `t-1`; the `t-1` low touches EMA20 but both prior lows remain above their
  respective EMA50. Short mirrors this two-bar countertrend reset.
- Decision confirmation at closed candle `t`: long closes above the `t-1` high with bullish body >= 0.30 ATR and close
  location in the upper 25% of its range. Short mirrors in the lower 25%.
- Liquidity proxy: decision close × base volume must be >= 1.25 times the median of the preceding 20 closed candles'
  close × volume. This is an OHLCV notional/participation proxy, not an order-book or aggressor-flow claim.
- The shared causal last-completed-4h BTC regime gate is bull for long and bear for short.
- The 1.25 ATR stop must be at least five times modelled round-trip cost. Stage-A accounting uses 5 bp taker fee plus
  2 bp adverse slippage on every fill and shared funding treatment.
- Fill is no earlier than the next open. Management is 1.25 ATR stop, half at +1R with breakeven, +2R final target,
  16-bar cap, trend-strength exit and eight-candle same-side lock.
- One frozen primary, no neighbour grid and no post-result symbol/regime/management selection.

## Stage-A chronology and gates

CORE-10 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. A side immediately fails on mean net R <= 0,
PF <= 1.05, non-positive pre-cost gross expectancy, or fewer than 60 closes. Only a development survivor runs unchanged:

1. `2025-04-01..2025-09-30`
2. `2025-10-01..2026-03-31`
3. `2026-04-01..2026-09-28` (exclusive)

Final Stage-A gate per side: positive aggregate mean net R, PF > 1.05, >= 100 closes over >= eight symbols, >= two of
three positive folds, median >= four closes/month, max drawdown <= 15R, and no symbol > 35% of absolute contribution.
Long and short cannot pool. The post-2026-09-28 holdout remains sealed.

Multiplicity: two new cells. Any failure is recorded and parked without rescue.

ROADMAP FIT: continuous cost-aware 15-minute candidate conveyor and frequent regime-qualified coverage.  
DEFERRED: Stage B, holdout, PAPER/testnet, runtime settings, mainnet and non-Binance venues.

## Run command

```powershell
py -3.14 tools/research/run_breadth_candidate.py --cache <15M_CACHE> --regime-cache <4H_CACHE> --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-10 --key trend_liquidity_pullback_15m --side long --mgmt defaults --out <OUT>\tlp15-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to the frozen folds.
