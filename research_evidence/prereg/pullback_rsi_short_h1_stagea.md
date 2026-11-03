# Candidate 23 / H1 pullback-RSI short preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-23 result  
Key: `pullback_rsi_short_h1`; timeframe: 4h; short only

## Queue identity and hypothesis

This is the next unused item in `STRATEGY_RESEARCH_BOARD_2026-10-23.md`: H1, regime-aware `pullback_rsi` short. It is an
exact default-off clone of the existing raw short cell and existing management, not a revised family. In a completed 4h
downtrend (EMA50 < EMA200 and close < EMA200), short when RSI14 crosses down through 60. Enter next 4h open.

## Frozen execution and costs

- Existing management unchanged: 2 ATR stop, half off at +1R, breakeven at +1R, final +3R target, 60-bar cap.
- Existing no-reentry-lock behavior unchanged. No pyramiding, parameter grid, symbol/regime selection or alternate exit.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified Binance funding at each held
  event using the last causal verified 1h mark close. Default-off/research-only; no PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if pre-cost gross <=0, mean net R <=0,
PF <=1.05, <100 closes, <8 symbols, max DD >15R, or <20 closes in either reported BTC 4h EMA200 regime. A survivor runs
unchanged on `2025-04-01..2025-09-30`, `2025-10-01..2026-03-31`, and `2026-04-01..2026-09-28` (exclusive). Final gate:
aggregate mean net R >0, PF >1.05, >=200 closes across >=8 symbols, >=2/3 positive folds, median >=8 closes/month, max
DD <=15R, neither regime mean R below -0.05, and no symbol >35% absolute contribution. Sealed holdout stays untouched.
Multiplicity: one queued short cell; all earlier results remain in the tested-candidate ledger.

ROADMAP FIT: queued H1 re-evaluation under current cost/evidence standard.  
DEFERRED: H2-H4, holdout, Stage B, PAPER/testnet, runtime, mainnet and all rescue.

## Run command

```powershell
python tools/research/run_pullback_rsi_short_h1_candidate.py `
  --cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 `
  --out C:\Dev\ZackBot_cowork_data\prs4h_h1\prs-short-dev.json
```
