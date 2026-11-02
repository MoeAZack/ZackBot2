# AOF-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any AOF-1H result  
Key: `aggressor_flow_absorption_1h`; timeframe: 1h; long and short judged separately

## Hypothesis and independent information

Extreme aggressive buying or selling that produces little hourly price progress can indicate absorption by passive
liquidity and subsequent aggressor exhaustion. AOF-1H uses actual taker-buy quote volume from checksum-verified Binance
1m archives, a field absent from the coarse cache. It is not a price oscillator, jump, wick, trend, rank, funding, mark-
basis, calendar or signed-close-volume rule. Opportunities arise asynchronously by symbol.

## Frozen causal rules

- For exactly 60 verified minutes in a completed hour, imbalance is `2 * taker-buy quote volume / total quote volume -
  1`. Long when imbalance <=-20%; short mirrors at >=+20%. Require absolute completed-hour open-to-close move <=0.40%
  (absorption). Entry is the next 1h open.
- Require a 1.5 ATR stop to cover at least 5x the 5 bp taker + 2 bp adverse-slippage round trip. Exit on EMA9 mean touch
  or via 1.5 ATR stop, 1.5R target or eight-hour cap. No partials, pyramiding, side pooling, symbol/time selection or grid.
- Actual funding uses verified settlement rates and causal archived mark closes. Incomplete 1m hours cannot signal.

## Stage-A chronology and frozen gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side reject: mean net R <=0, PF <=1.05,
non-positive pre-cost gross expectancy, <100 closes, <8 symbols, max drawdown >15R, or <20 closes in either BTC 4h
EMA200 regime. A survivor runs unchanged on the three established chronological folds. Final per-side gate: aggregate
mean net R >0, PF >1.05, >=200 closes across >=8 symbols, >=2/3 positive folds, median >=8 closes/month, max drawdown
<=15R, neither regime mean R below -0.05, and no symbol >35% absolute contribution. Sealed holdout stays closed.
Multiplicity: two new cells; all earlier failures and parked observations remain separate.

ROADMAP FIT: authoritative aggressor-flow candidate 20 after candidate 19's price-jump cost failure.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet, tuning and subgroup selection.

## Run command

```powershell
python tools/research/run_aggressor_flow_absorption_candidate.py --cache C:\Dev\ZackBot_research_cache\candles_1h_fib02 `
  --regime-cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long `
  --out C:\Dev\ZackBot_cowork_data\aof1h_stagea\aof-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to chronological folds.
