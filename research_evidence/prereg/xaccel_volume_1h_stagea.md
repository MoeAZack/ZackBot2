# XAV-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any XAV-1H market-data result  
Key: `xaccel_volume_1h`; timeframe: 1h; long and short judged separately

## Hypothesis and independence

Cross-sectional acceleration—a recent increase in a symbol's own momentum—may capture a different return source than
ranking momentum level. XAV-1H selects new acceleration leaders/laggards only when absolute trend, relative volume and a
causal completed-4h BTC trend regime agree. The 1h horizon reduces the fee burden seen in the five rejected 15m families.

This is not EDGE-03 `xsec_rs_4h`, which ranks 180-bar return divided by volatility once daily and holds 24 hours. XAV-1H
ranks `(latest 12h log return - preceding 12h log return) / prior 24h hourly volatility` every hour and admits only a
new top/bottom-two transition with volume. It is also distinct from runtime momentum rank, EMA crosses, hot-coin level
rank, EDGE-02, VWAP, compression, pullbacks, Fibonacci, liquidity sweeps and DCA.

## Frozen causal mechanics

- At each closed 1h candle, compute 12h log return and subtract the preceding non-overlapping 12h log return. Divide by
  the standard deviation of hourly log returns over the preceding 24 candles, ending one candle before the decision.
- Rank all complete CORE-20 symbols by this acceleration value. Long requires a first transition into positive top two;
  short requires a first transition into negative bottom two. Ties use pandas' stable average rank and cannot be forced.
- Absolute trend: long close > EMA50 > EMA200 and 42h return > 0; short mirrors.
- Relative volume: decision volume >= 1.50 times median volume of the preceding 24 completed 1h candles.
- BTC regime: aggregate complete hourly BTC candles into completed four-hour blocks. Long requires BTC 4h close > EMA50
  > EMA200; short requires the exact mirror. An incomplete current 4h block is unavailable, never projected.
- The 1.5 ATR stop must be >= five times modelled round-trip cost. Accounting charges 5 bp taker fee plus 2 bp adverse
  slippage on every fill and shared signed funding. Entry is next open.
- Management: 1.5 ATR stop, half at +1R with breakeven, +2R final target, 12-hour cap, rank/trend/regime exit and
  same-decision-bar re-entry lock. One frozen primary, no parameter grid.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side-level reject: mean net R <= 0,
PF <= 1.05, non-positive pre-cost gross expectancy, fewer than 100 closes, or fewer than eight symbols with closes.
Only a development survivor runs unchanged:

1. `2025-04-01..2025-09-30`
2. `2025-10-01..2026-03-31`
3. `2026-04-01..2026-09-28` (exclusive)

Final Stage-A gate per side: aggregate mean net R > 0, PF > 1.05, >= 200 total closes over >= eight symbols, at least
two of three positive folds, median >= eight closes/month, max drawdown <= 15R, and no symbol > 35% of absolute
contribution. The sealed post-2026-09-28 holdout remains untouched. Sides cannot pool or rescue one another.

Multiplicity: two new cells. A failure is recorded and parked without tuning.

ROADMAP FIT: selective lower-turnover candidate after five cost-dominated 15m rejections.  
DEFERRED: Stage B, holdout, PAPER/testnet, runtime settings, mainnet and non-Binance venues.

## Run command

```powershell
py -3.14 tools/research/run_xaccel_candidate.py --cache <1H_CACHE> --regime-cache <4H_CACHE> --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long --out <OUT>\xav1h-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to the frozen folds.
