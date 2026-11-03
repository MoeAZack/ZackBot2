# Candidate 24 / H2 rotation-long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-24 result  
Key: `rotation_long_h2`; timeframe: 4h; long only

## Queue identity and hypothesis

This is the next unused item in `STRATEGY_RESEARCH_BOARD_2026-10-23.md`: H2, existing `rotation` long. It is an exact
default-off clone. On completed 4h candles, rank the static universe by 30-day risk-adjusted return; enter when a symbol
moves into the top five while above EMA50. Exit when rank falls below eight or close falls below EMA50. Entry fills at
the next 4h open.

## Frozen execution and costs

- Existing rules unchanged: k=5, 3 ATR stop, signal exit, no target/time cap, no re-entry lock or pyramiding.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified Binance funding using causal 1h
  mark closes. No parameter grid, symbol/regime selection or alternate management.
- Default-off/research-only. No PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if pre-cost gross <=0, mean net R <=0,
PF <=1.05, <100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC 4h EMA200 regime. A survivor runs unchanged
on the three established chronological folds. Final gate: aggregate mean net R >0, PF >1.05, >=200 closes across >=8
symbols, >=2/3 positive folds, median >=8 closes/month, max DD <=15R, neither regime mean R below -0.05, and no symbol
>35% absolute contribution. Sealed holdout stays untouched. Multiplicity: one queued long cell; earlier trials remain in
the tested-candidate ledger.

ROADMAP FIT: queued H2 re-evaluation under current cost/evidence standard.  
DEFERRED: H3-H4, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.

## Run command

```powershell
python tools/research/run_rotation_long_h2_candidate.py `
  --cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 `
  --out C:\Dev\ZackBot_cowork_data\rot4h_h2\rot-long-dev.json
```
