# TJD-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any TJD-1H result  
Key: `transient_jump_dislocation_1h`; timeframe: 1h; long and short judged separately

## Hypothesis and independent information

A large, high-participation one-minute futures jump that is not retained by its completed hour can reflect transient
liquidation or aggressive-flow dislocation rather than new information. TJD-1H uses checksum-verified Binance 1m paths
inside each hour. It is not a coarse-candle wick, rolling-range sweep, trend, oscillator, rank, funding, mark-basis,
calendar or volume-pressure strategy, and opportunities occur asynchronously by symbol.

## Frozen causal rules

- After each completed 1h candle, find the largest absolute 1m open-to-close return among exactly 60 verified minute
  records. Require magnitude >=0.60%, that minute's quote volume >=2x the hour's median minute quote volume, and absolute
  completed-hour return <=50% of the jump magnitude. Fade the jump sign at the next 1h open.
- Require a 1.5 ATR stop to cover at least 5x the 5 bp taker + 2 bp adverse-slippage round trip. Exit on EMA9 mean touch
  or via 1.5 ATR stop, 1.5R target or eight-hour cap. No partials, pyramiding, side pooling, symbol/time selection or grid.
- Actual funding uses verified settlement rates and causal archived mark closes. Incomplete 1m hours cannot signal.

## Stage-A chronology and frozen gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side reject: mean net R <=0, PF <=1.05,
non-positive pre-cost gross expectancy, <100 closes, <8 symbols, max drawdown >15R, or <20 closes in either BTC 4h
EMA200 regime. A survivor runs unchanged on the three established chronological folds. Final per-side gate: aggregate
mean net R >0, PF >1.05, >=200 closes across >=8 symbols, >=2/3 positive folds, median >=8 closes/month, max drawdown
<=15R, neither regime mean R below -0.05, and no symbol >35% absolute contribution. Sealed holdout stays closed.
Multiplicity: two new cells; all prior failures/parked observations remain separate.

ROADMAP FIT: asynchronous archived-microstructure candidate 19 after synchronized candidate 18.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet, tuning and subgroup selection.

## Run command

```powershell
python tools/research/run_transient_jump_dislocation_candidate.py --cache C:\Dev\ZackBot_research_cache\candles_1h_fib02 `
  --regime-cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long `
  --out C:\Dev\ZackBot_cowork_data\tjd1h_stagea\tjd-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to chronological folds.
