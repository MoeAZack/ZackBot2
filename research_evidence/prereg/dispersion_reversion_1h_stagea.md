# DSR-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any DSR-1H market-data result  
Key: `dispersion_reversion_1h`; timeframe: 1h; long and short judged separately

## Hypothesis and independence

When cross-asset BTC-neutral returns become unusually dispersed, the most extreme residual moves may partially revert
after they begin recovering. DSR-1H uses a cross-sectional robust extreme and enters the recovery, not the initial shock.

This is not EDGE-02 `btc_residual_mr.v1`: that rule uses 15m zero-intercept beta, a 32-bar residual sum, each symbol's own
1,920-observation time-series z-score and a range-only regime. DSR-1H uses 1h returns, a prior 30-day rolling beta, a
six-hour residual sum, same-time cross-sectional median/MAD standardization, a dispersion event and recovery entry. It is
also distinct from EDGE-03 ranks, runtime strategies and candidates 1–8.

## Frozen causal mechanics

- Estimate each symbol's zero-intercept BTC beta from the preceding 720 hourly returns, ending at `t-1`, requiring 576.
- Current residual is symbol return minus beta × BTC return. Sum the latest six residuals.
- At each hour, across symbols with valid values, compute median and MAD; robust z is deviation / (`1.4826 × MAD`). At
  least eight symbols are required. BTC is the benchmark and cannot emit a non-zero residual extreme.
- Elevated dispersion requires current MAD above the causal trailing-168h 75th percentile ending at `t-1`, with at
  least 120 prior observations. The entry uses the prior candle's elevated-dispersion state.
- Long: prior robust z <= -2.50, current z crosses above -2.50 but remains <= -0.50, and candle is bullish. Short mirrors
  recovery below +2.50 while remaining >= +0.50 on a bearish candle.
- Current close × volume must be >= 0.80 times its preceding-24h median. The 1.5 ATR stop must be >= five times round-trip
  cost. Stage A charges 5 bp taker fee plus 2 bp adverse slippage on each fill and shared signed funding.
- Entry is next open. Exit signal is z reaching zero or adverse 3.50; management is 1.5 ATR stop, half at +0.8R with
  breakeven, +1.5R final target, 12-hour cap and same-decision-bar re-entry lock.
- One frozen primary, no threshold grid or post-result symbol selection.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side-level reject: mean net R <= 0,
PF <= 1.05, non-positive pre-cost gross expectancy, fewer than 100 closes, or fewer than eight symbols with closes.
Only a development survivor runs unchanged:

1. `2025-04-01..2025-09-30`
2. `2025-10-01..2026-03-31`
3. `2026-04-01..2026-09-28` (exclusive)

Final Stage-A gate per side: aggregate mean net R > 0, PF > 1.05, >= 200 closes over >= eight symbols, at least two of
three positive folds, median >= eight closes/month, maximum drawdown <= 15R, and no symbol >35% of absolute contribution.
The sealed post-2026-09-28 holdout remains untouched. Sides cannot pool or rescue one another.

Multiplicity: two new cells. Failure is recorded and parked without tuning.

ROADMAP FIT: orthogonal BTC-neutral cross-asset reversion after trend-family failures.  
DEFERRED: Stage B, holdout, PAPER/testnet, runtime settings, mainnet and non-Binance venues.

## Run command

```powershell
py -3.14 tools/research/run_dispersion_reversion_candidate.py --cache <1H_CACHE> --regime-cache <4H_CACHE> --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long --out <OUT>\dsr1h-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to the frozen folds.
