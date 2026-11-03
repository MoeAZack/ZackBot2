# WLP-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any WLP-1H result  
Key: `weekend_liquidity_premium_1h`; timeframe: 1h; long and short judged separately

## Hypothesis and independence

Crypto's weekend open and close change institutional participation and liquidity while the market remains continuously
tradable. WLP-1H tests whether bearing that fixed transition risk earns a directional premium. It is a calendar-risk
hypothesis with no price, return, trend, oscillator, rank, volume, funding, basis or volatility-conditioned selection.
Long and short are independent cells rather than choosing the better direction after inspection.

## Frozen causal rules

- A signal exists only after the Friday 23:00 UTC candle and Sunday 23:00 UTC candle close; entry is respectively the
  Saturday 00:00 and Monday 00:00 next open. Both side cells use the same transition set.
- Require a 2.5 ATR stop to cover at least 5x the 5 bp taker + 2 bp adverse-slippage round trip. Exit only via 2.5 ATR
  stop, 2.5R target or 24-hour cap. No partials, pyramiding, side pooling, boundary/symbol selection or parameter grid.
- Actual checksum-verified Binance funding is booked from verified event rates and causal 1h mark closes.

## Stage-A chronology and frozen gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side reject: mean net R <=0, PF <=1.05,
non-positive pre-cost gross expectancy, <100 closes, <8 symbols, max drawdown >15R, or <20 closes in either reported BTC
4h EMA200 regime. A survivor runs unchanged on the three established chronological folds. Final per-side gate: aggregate
mean net R >0, PF >1.05, >=200 closes across >=8 symbols, >=2/3 positive folds, median >=8 closes/month, max drawdown
<=15R, neither regime mean R below -0.05, and no symbol >35% absolute contribution. Sealed holdout stays closed.
Multiplicity: two new cells; prior failures and parked observations remain separate.

ROADMAP FIT: low-turnover calendar-risk candidate 18 after cost-heavy candidate 17.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet, tuning and subgroup selection.

## Run command

```powershell
python tools/research/run_weekend_liquidity_premium_candidate.py --cache C:\Dev\ZackBot_research_cache\candles_1h_fib02 `
  --regime-cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long `
  --out C:\Dev\ZackBot_cowork_data\wlp1h_stagea\wlp-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to chronological folds.
