# VRC-15 Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any VRC-15 market-data result  
Key: `vwap_reclaim_15m`; timeframe: 15m; sides: long and short as separate cells

## Hypothesis and non-duplication

Crypto often trends intraday but revisits the day's volume-weighted consensus before continuing. A shallow reclaim of the
UTC-session VWAP, confirmed by a prior-candle break, real body and participation, may capture those continuations after
fees without buying the original impulse.

This is not FIB-PULLBACK-01/02 (no pivots or Fibonacci levels), EDGE-02 RSI reclaim/squeeze/structure/BTC residual (no RSI,
Donchian compression, swing break or BTC-beta residual), EDGE-03 cross-sectional strength (single-symbol time-series rule),
SC-QBANK (session anchor plus volume/break/cost confirmation, not an EMA9 touch), or the 4h `exp_vwaprev` mean-reversion rule
(opposite mechanism and 15m continuation timeframe). Those prior trials remain in the candidate ledger.

## Frozen causal rule

- UTC-session VWAP before candle `i` = cumulative typical-price × positive base volume / cumulative positive base volume
  from the current UTC day through `i-1`. Candle `i` and all future candles are excluded. At least 8 earlier session candles.
- Trend: EMA20 > EMA50 and close > EMA50 for long; exact mirror for short.
- Reset and reclaim (long): prior close is at/below current prior-only session VWAP, no more than 1.0 ATR below it; decision
  close is above VWAP and above the prior high. Short is mirrored.
- Confirmation: with-trend real body >= 0.20 ATR; volume >= median of the preceding 20 candles.
- Cost floor: 1.25 ATR stop distance >= 4 × modelled round-trip cost. Cost model in the runner is 5 bp taker fee plus 2 bp
  adverse slippage on each fill, plus the shared 15m funding allocation.
- Management: next-open entry, 1.25 ATR stop, 50% at +1R and breakeven, full target +1.8R, 16-bar maximum; exit on a close
  back through session VWAP or EMA20/50 flip. Re-entry lock: 8 candles.
- No parameter selection. These values are one frozen primary. Any alternative is a separately counted candidate.

## Stage-A evaluation

Use the shared bounded breadth runner on the frozen development window, CORE-20 first, one side per packet. Candles must be
complete on the 15m grid and end before the sealed cutoff. Report raw signals, floor skips, trades, gross/net P&L and R,
fees, funding, drawdown, per-symbol rows and regime rows. Expected measurement rate is roughly 2-8 portfolio entries/day
across CORE-20; this is a hypothesis, not a gate or result.

Chronological decision sequence:

1. Development screen: 2024-10-14 through 2025-03-31.
2. Walk-forward folds, never shuffled: 2025-04-01..2025-09-30, 2025-10-01..2026-03-31,
   2026-04-01..2026-09-28 exclusive. Parameters stay frozen across folds.
3. The sealed post-2026-09-28 holdout is untouched. Development and walk-forward cannot promote directly to mainnet.

Immediate reject: either side has <= 0 mean net R in development, or aggregate cost drag exceeds gross expectancy.
Stage-A survivor: positive mean net R, >= 100 closed trades over >= 8 symbols, >= 4 trades/month median, positive in at least
2/3 chronological folds, and no single symbol contributes > 35% of net R. A survivor only qualifies for independent review
and a separately reviewed PAPER canary; it is not a profitability claim.

Multiplicity: VRC-15 long and short are two new counted cells. No post-result session, symbol, regime or management filter.

ROADMAP FIT: continuous cost-aware 15m strategy conveyor; restores measured opportunity throughput without enlarging risk.  
DEFERRED: market-data verdict, holdout, PAPER activation, runtime settings, mainnet, credentials and non-Binance venues.

## Run command

```powershell
py -3.14 tools/research/run_breadth_candidate.py --cache <15M_CACHE> --regime-cache <4H_CACHE> --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --key vwap_reclaim_15m --side long --mgmt defaults --out <OUT>\vrc15-long-dev.json
```

Repeat with `--side short`; subsequent commands use the three frozen chronological windows above.
