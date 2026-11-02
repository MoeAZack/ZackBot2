# ASS-4H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any ASS-4H result  
Key: `atr_shock_snapback_4h`; timeframe: 4h; long and short judged separately

## Hypothesis and independence

A large counter-trend four-hour shock may temporarily exhaust liquidity; reclaiming its midpoint on the next completed
bar may offer a short snapback while remaining aligned with the slow trend. ASS-4H does not use cross-sectional ranks
(RBR/EDGE-03), ADX/DMI (TLP-15), VWAP, compression, breakout/retest, Fibonacci, funding as a signal, or multi-timeframe
breakout state. It is a two-bar time-series event with three economic parameters fixed before results.

## Frozen causal rules

- Slow trend at both shock and recovery closes: EMA50 > EMA200 for long, exact mirror for short.
- Long: preceding candle body <= -1.5 ATR(14); current candle bullish and closes above the preceding body's midpoint.
  Short mirrors an >= +1.5 ATR prior body and bearish midpoint loss.
- Current volume >=80% of the preceding 42-bar median. The 1.5 ATR stop must be >=5x the 5 bp taker + 2 bp adverse
  slippage round trip. Signal on close; entry next 4h open.
- Exit at EMA20 mean touch or slow-trend invalidation; frozen protection is 1.5 ATR stop, half at +0.8R with breakeven,
  +1.5R final target, 12-bar cap. No pyramiding; same-decision-bar re-entry locked.
- Actual checksum-verified Binance funding is signed by side using the last verified 1h mark close available at each
  event. Every fill pays frozen taker fee and adverse slippage. No parameter grid or symbol selection.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side reject: mean net R <=0, PF <=1.05,
non-positive pre-cost gross expectancy, <80 closes, <8 symbols, max drawdown >15R, or <15 closes in either reported
BTC-above/below-EMA200 regime. Only a survivor runs unchanged on:

1. `2025-04-01..2025-09-30`
2. `2025-10-01..2026-03-31`
3. `2026-04-01..2026-09-28` (exclusive)

Final gate per side: aggregate mean net R >0, PF >1.05, >=160 closes over >=8 symbols, >=2/3 positive folds, median >=5
closes/month, max drawdown <=15R, neither regime mean R below -0.05, and no symbol >35% of absolute contribution. The
sealed post-2026-09-28 holdout remains closed. Multiplicity: two cells. Failure is parked without rescue.

ROADMAP FIT: simple lower-turnover 4h trend/mean-reversion complement after candidate 12.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet, tuning and symbol pruning.

## Run command

```powershell
python tools/research/run_atr_shock_snapback_candidate.py --cache <4H_CACHE> `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z `
  --tier CORE-20 --side long --out <OUT>\ass-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to folds.
