# RSV-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any RSV-1H result  
Key: `realized_skew_valuation_1h`; timeframe: 1h; long and short judged separately

## Hypothesis and independence

Extremely positive realized skew can reflect crowded lottery demand; extremely negative skew can reflect forced-selling
asymmetry. RSV-1H tests a fixed, symmetric valuation reversal once daily. Its information variable is the third moment
of completed returns, not trend, return rank, BTC residual, volume pressure, funding, VWAP, breakout, wick sweep,
compression, session handoff or an existing runtime rule.

## Frozen causal rules

- At each completed 00:00 UTC candle, calculate sample skew of the last 168 completed hourly close returns. Long when
  skew <=-0.75; short mirrors at >=+0.75. Entry is the next hourly open.
- Require the 2 ATR stop to cover at least 5x the 5 bp taker + 2 bp adverse-slippage round trip. Exit when skew crosses
  zero or via a 2 ATR stop, 2R target or 24-hour cap. No partials, pyramiding, side pooling, symbol selection or grid.
- Actual checksum-verified Binance funding is booked using the last verified 1h mark close at each settlement.

## Stage-A chronology and frozen gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side reject: mean net R <=0, PF <=1.05,
non-positive pre-cost gross expectancy, <100 closes, <8 symbols, max drawdown >15R, or <20 closes in either reported BTC
4h EMA200 regime. A survivor runs unchanged on `2025-04-01..2025-09-30`, `2025-10-01..2026-03-31`, and
`2026-04-01..2026-09-28` (exclusive). Final per-side gate: aggregate mean net R >0, PF >1.05, >=200 closes across >=8
symbols, >=2/3 positive folds, median >=8 closes/month, max drawdown <=15R, neither regime mean R below -0.05, and no
symbol >35% of absolute contribution. Sealed holdout stays closed. Multiplicity: two new cells; all prior failures and
parked observations remain separate.

ROADMAP FIT: lower-turnover higher-density orthogonal distribution-shape factor after candidate 15.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet, tuning and subgroup selection.

## Run command

```powershell
python tools/research/run_realized_skew_valuation_candidate.py --cache C:\Dev\ZackBot_research_cache\candles_1h_fib02 `
  --regime-cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long `
  --out C:\Dev\ZackBot_cowork_data\rsv1h_stagea\rsv-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to chronological folds.
