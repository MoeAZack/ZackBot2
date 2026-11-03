# BOL-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any BOL-1H result  
Key: `btc_orderflow_leadlag_1h`; timeframe: 1h; long and short judged separately

## Hypothesis and independent information

Information can reach BTC derivatives before individual altcoins. BOL-1H tests whether a verified BTC aggressor-flow
impulse diffuses into a target that remained locally flat during the completed impulse hour. This is cross-asset lead-
lag, not candidate 20's same-asset absorption/inversion, and not a trend/rank/residual/funding/basis/calendar rule.

## Frozen causal rules

- From exactly 60 archived BTC minutes, compute completed-hour taker imbalance. Long a target when BTC imbalance >=+15%
  and BTC open-to-close move >=+0.60%; short mirrors both signs. Target completed-hour absolute move must be <=0.20%.
  Entry is the target's next 1h open.
- Require a 2 ATR stop to cover at least 5x the 5 bp taker + 2 bp adverse-slippage round trip. Exit only via 2 ATR stop,
  2R target or 12-hour cap. No partials, pyramiding, side pooling, symbol/time selection or grid.
- Actual funding uses verified settlement rates and causal mark closes. Missing/incomplete BTC minute hours cannot signal.

## Stage-A chronology and frozen gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side reject: mean net R <=0, PF <=1.05,
non-positive pre-cost gross expectancy, <100 closes, <8 symbols, max drawdown >15R, or <20 closes in either BTC 4h
EMA200 regime. A survivor runs unchanged on the three established chronological folds. Final per-side gate: aggregate
mean net R >0, PF >1.05, >=200 closes across >=8 symbols, >=2/3 positive folds, median >=8 closes/month, max drawdown
<=15R, neither regime mean R below -0.05, and no symbol >35% absolute contribution. Sealed holdout stays closed.
Multiplicity: two new cells; prior failures and parked observations remain separate.

ROADMAP FIT: cross-asset information-diffusion candidate 21 after same-asset absorption failed.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet, tuning and subgroup selection.

## Run command

```powershell
python tools/research/run_btc_orderflow_leadlag_candidate.py --cache C:\Dev\ZackBot_research_cache\candles_1h_fib02 `
  --regime-cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long `
  --out C:\Dev\ZackBot_cowork_data\bol1h_stagea\bol-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to chronological folds.
