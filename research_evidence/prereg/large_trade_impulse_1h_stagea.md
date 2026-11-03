# LTI-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any LTI-1H result  
Key: `large_trade_impulse_1h`; timeframe: 1h; long and short judged separately

## Hypothesis and independent information

A directional move dominated by unusually large average ticket size may represent informed participation and persist.
LTI-1H uses verified Binance 1m quote volume divided by actual trade count, compared with its own prior seven-day
baseline. It is distinct from raw volume, taker-side imbalance, BTC lead-lag, one-minute jump, trend rank, funding, basis,
calendar or price-only momentum rules.

## Frozen causal rules

- For exactly 60 verified minutes, sum quote volume and trade count. Average trade notional must be >=2x the median of
  the preceding 168 completed hourly values. Long when the completed hour returns >=+0.60%; short mirrors at <=-0.60%.
  Entry is the next 1h open.
- Require a 2 ATR stop to cover at least 5x the 5 bp taker + 2 bp adverse-slippage round trip. Exit only via 2 ATR stop,
  2R target or 12-hour cap. No partials, pyramiding, side pooling, symbol/time selection or grid.
- Actual funding uses verified settlement rates and causal mark closes. Incomplete 1m hours cannot signal.

## Stage-A chronology and frozen gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side reject: mean net R <=0, PF <=1.05,
non-positive pre-cost gross expectancy, <100 closes, <8 symbols, max drawdown >15R, or <20 closes in either BTC 4h
EMA200 regime. A survivor runs unchanged on three established chronological folds. Final per-side gate: mean net R >0,
PF >1.05, >=200 closes across >=8 symbols, >=2/3 positive folds, median >=8 closes/month, max drawdown <=15R, neither
regime mean R below -0.05, and no symbol >35% absolute contribution. Sealed holdout stays closed. Multiplicity: two new
cells; all prior failures/parked observations remain separate.

ROADMAP FIT: authoritative large-ticket participation candidate 22.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet, tuning and subgroup selection.

## Run command

```powershell
python tools/research/run_large_trade_impulse_candidate.py --cache C:\Dev\ZackBot_research_cache\candles_1h_fib02 `
  --regime-cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long `
  --out C:\Dev\ZackBot_cowork_data\lti1h_stagea\lti-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to chronological folds.
