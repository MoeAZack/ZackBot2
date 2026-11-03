# Candidate 29 / EMA-supertrend short preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-29 result  
Key: `ema_st_short_c29`; timeframe: 4h; short only

## Queue identity and hypothesis

Previously considered long DCA, range and 15m structure cells were skipped because their exact development windows are
already spent. Candidate 29 is the existing `sig_ema_st` function's previously off-registry short cell, cloned
default-OFF without changing the emitted rule: on a completed 4h candle, EMA20 crosses below EMA50 while close is below
EMA200 and Supertrend is bearish. Exit when EMA20 rises above EMA50 or Supertrend ceases to be bearish. Fill next open.

## Frozen execution and costs

- Original management unchanged: 2.5 ATR stop plus signal exit; no target, time cap, re-entry lock or pyramiding.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified Binance funding using causal 1h
  mark closes. No parameter grid or symbol/regime selection.
- Default-off/research-only. No PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if pre-cost gross <=0, mean net R <=0,
PF <=1.05, <100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC 4h EMA200 regime. Every development gate
is inspected before folds. A survivor runs unchanged on three chronological folds. Final: mean net R >0, PF >1.05,
>=200 closes across >=8 symbols, >=2/3 positive folds, median >=8 closes/month, max DD <=15R, neither regime mean R
below -0.05, and no symbol >35% absolute contribution. Sealed holdout stays untouched.

ROADMAP FIT: next genuinely untested emitted board cell after candidate 28.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.

## Run command

```powershell
python tools/research/run_ema_st_short_c29_candidate.py `
  --cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 `
  --out C:\Dev\ZackBot_cowork_data\emastshort4h_c29\ema-st-short-dev.json
```
