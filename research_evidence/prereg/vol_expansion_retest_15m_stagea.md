# VER-15 Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any VER-15 market-data result  
Key: `vol_expansion_retest_15m`; timeframe: 15m; long and short are separate cells

## Hypothesis and independence

After a genuine volatility expansion, the first orderly return to the broken range edge can offer a better continuation
entry than chasing the breakout. VER-15 therefore requires a completed compression bar, a completed expansion breakout,
and a separate completed retest/reclaim candle before a next-open fill.

This is not EDGE-02 `squeeze_release.v1`, which enters the first Donchian release; VER-15 refuses that entry and tests the
subsequent retest. It does not use RSI exhaustion, swing structure, BTC residuals, VWAP, Fibonacci levels, cross-sectional
ranks, funding/OI, DCA, or any previously rejected fast strategy rule. Those trials remain counted in the ledger.

## Frozen causal mechanics

- At decision candle `t`, compression is Bollinger-width percentile `<= 0.15` at `t-2`.
- The range edge is the high/low of the 32 candles preceding breakout candle `t-1`, excluding that breakout candle.
- At `t-1`, long requires a close above the frozen high after the previous close was not above its then-known high;
  short is mirrored. Breakout body must be `>= 0.50 ATR(14)` and volume `>= 1.50` times the mean of the 20 candles before
  the breakout. No decision uses candle `t` or later to define the breakout level.
- At `t`, long must touch the frozen high plus at most `0.15 ATR` and close at least `0.20 ATR` above it; short mirrors.
- Trend must agree on breakout and retest: EMA20 > EMA50 > EMA200 for long, with EMA50 above its value eight candles ago;
  exact mirror for short. The shared causal BTC 4h regime gate is bull for long and bear for short.
- Cost admission requires the `1.25 ATR` stop distance to be at least five times modelled round-trip cost. Stage-A costs
  are 5 bp taker fee plus 2 bp adverse slippage on each fill, plus shared funding accounting.
- Entry is no earlier than the open following `t`. Management is 1.25 ATR stop, half at +1R with breakeven, +2R final
  target, 16-bar cap, close-through-range/trend-flip exit, and an eight-candle re-entry lock.
- There is one frozen primary only. No threshold, symbol, regime, or management selection follows the result.

## Stage-A chronology and gates

Universe is CORE-10 first. Development is `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. An immediate reject is mean
net R `<= 0`, PF `<= 1.05`, non-positive pre-cost gross expectancy, or fewer than 60 trades on either separately judged
side. Only a development survivor runs three unchanged chronological folds:

1. `2025-04-01..2025-09-30`
2. `2025-10-01..2026-03-31`
3. `2026-04-01..2026-09-28` (exclusive)

A Stage-A survivor needs positive mean net R, PF > 1.05, at least 100 total closes over at least eight symbols, at least
two of three positive folds, median activity >= four closes/month, maximum drawdown <= 15R, and no symbol above 35% of
absolute contribution. The sealed post-2026-09-28 holdout stays untouched. Long and short cannot rescue one another.

Multiplicity: VER-15 long and short add two cells. Failure is recorded and parked without post-result tuning.

ROADMAP FIT: continuous cost-aware 15-minute candidate conveyor and regime-diverse opportunity coverage.  
DEFERRED: execution-grade Stage B, holdout, PAPER/testnet, runtime settings, mainnet and non-Binance venues.

## Run command

```powershell
py -3.14 tools/research/run_breadth_candidate.py --cache <15M_CACHE> --regime-cache <4H_CACHE> --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-10 --key vol_expansion_retest_15m --side long --mgmt defaults --out <OUT>\ver15-long-dev.json
```

Repeat unchanged for `--side short`. Only a development survivor proceeds to the three frozen folds.
