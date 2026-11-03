# Candidate 27 / cold-coin momentum short preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-27 result  
Key: `exp_mom_short_c27`; timeframe: 4h; short only

## Queue identity and hypothesis

After H1-H4, this is the next unused existing cell on the strategy board: exact `exp_mom_short`. It is absent from the
completed STATIC-40 breadth ledger and is cloned default-OFF without changing its rules. On a completed 4h candle, short
a bottom-three 7-day relative mover when volume is >1.5 times its 42-bar average and price closes below the prior 20-bar
low and EMA50. Exit above EMA20; fill the signal at next open.

## Frozen execution and costs

- Existing management unchanged: 2 ATR stop, half off at 2R, 2.5 ATR trail, signal exit; no time cap or pyramiding.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified Binance funding using causal 1h
  mark closes. No parameter grid, symbol/regime selection or alternate management.
- Default-off/research-only. No PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if pre-cost gross <=0, mean net R <=0,
PF <=1.05, <100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC 4h EMA200 regime. Every development gate
is inspected before any fold launch. A survivor runs unchanged on the three established chronological folds. Final gate:
aggregate mean net R >0, PF >1.05, >=200 closes across >=8 symbols, >=2/3 positive folds, median >=8 closes/month,
max DD <=15R, neither regime mean R below -0.05, and no symbol >35% absolute contribution. Sealed holdout stays untouched.

ROADMAP FIT: first unused board-cell scout after queued H1-H4.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.

## Run command

```powershell
python tools/research/run_exp_mom_short_c27_candidate.py `
  --cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 `
  --out C:\Dev\ZackBot_cowork_data\expmomshort4h_c27\exp-mom-short-dev.json
```
