# Candidate 28 / rip-sell DCA short preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-28 result  
Key: `rip_sell_dca_c28`; timeframe: 4h; short only

## Queue identity and hypothesis

`bear_breakdown` was skipped because its 2021-2026 legacy window is already marked spent. This is the next ledger-clean
existing board cell: exact `exp_rip_sell_dca`, cloned default-OFF. On a completed 4h candle with EMA50 below EMA200, short
the first close crossing above 1.5 ATR over EMA20. Fill next open. This evaluates the existing basket, not a new variant.

## Frozen execution and costs

- Existing management unchanged: up to three 1.5x safety orders one ATR apart, basket target one ATR, hard basket stop
  two ATR beyond the last order, 60-bar cap. No alternate sizing, cap or single-entry diagnostic can rescue it.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified Binance funding using causal 1h
  mark closes. No parameter grid or symbol/regime selection.
- Default-off/research-only. No PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if pre-cost gross <=0, mean net R <=0,
PF <=1.05, <100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC 4h EMA200 regime. Every development gate
is inspected before any fold launch. A survivor runs unchanged on three chronological folds. Final: mean net R >0,
PF >1.05, >=200 closes across >=8 symbols, >=2/3 positive folds, median >=8 closes/month, max DD <=15R, neither regime
mean R below -0.05, and no symbol >35% absolute contribution. Sealed holdout stays untouched.

ROADMAP FIT: next unused existing board cell after candidate 27.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.

## Run command

```powershell
python tools/research/run_rip_sell_dca_c28_candidate.py `
  --cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 `
  --out C:\Dev\ZackBot_cowork_data\ripselldca4h_c28\rip-sell-dca-dev.json
```
