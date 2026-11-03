# Candidate 26 / H4 ema-momentum-long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-26 result  
Key: `ema_mom_long_h4`; timeframe: 4h; long only

## Queue identity and hypothesis

This is the next unused item in `STRATEGY_RESEARCH_BOARD_2026-10-23.md`: H4, existing `ema_mom` long. It is an exact
default-off clone. On completed 4h candles, enter when EMA20 crosses above EMA50 while close is above EMA200 and both
42-bar and 180-bar returns are positive. Exit when EMA20 falls below EMA50 or either momentum condition is lost. Fill
next open. H4 shares H3's entry but is a distinct frozen management cell: original 2.5 ATR stop and signal exit only,
without H3's 3R target or 180-bar cap.

## Frozen execution and costs

- Existing rules unchanged: 2.5 ATR stop and signal exit; no target, time cap, re-entry lock or pyramiding.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified Binance funding using causal 1h
  mark closes. No parameter grid, symbol/regime selection or alternate management.
- Default-off/research-only. No PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if pre-cost gross <=0, mean net R <=0,
PF <=1.05, <100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC 4h EMA200 regime. Every development gate
is inspected before any fold launch. A survivor runs unchanged on the three established chronological folds. Final gate:
aggregate mean net R >0, PF >1.05, >=200 closes across >=8 symbols, >=2/3 positive folds, median >=8 closes/month,
max DD <=15R, neither regime mean R below -0.05, and no symbol >35% absolute contribution. Sealed holdout stays untouched.
Multiplicity: one queued long management cell; H3 and earlier trials remain in the tested-candidate ledger.

ROADMAP FIT: queued H4 re-evaluation under current cost/evidence standard.  
DEFERRED: further scouting, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.

## Run command

```powershell
python tools/research/run_ema_mom_long_h4_candidate.py `
  --cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 `
  --out C:\Dev\ZackBot_cowork_data\emamom4h_h4\ema-mom-long-dev.json
```
