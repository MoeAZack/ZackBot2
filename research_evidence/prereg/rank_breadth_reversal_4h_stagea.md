# RBR-4H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any RBR-4H result  
Key: `rank_breadth_reversal_4h`; timeframe: 4h; long and short judged separately

## Hypothesis and tested-ledger independence

The existing rotation/EDGE-03 family follows cross-sectional strength. RBR-4H asks the opposite, lower-turnover question:
after the whole universe becomes directionally crowded, can an extreme seven-day laggard/leader recover enough to pay
costs? It is not DSR-1H (BTC-neutral six-hour residual/MAD event), EDGE-02 residual reversion, a VWAP/range rule, or a
trend/breakout continuation. Candidate rules were selected from the tested-family ledger before results; candidates 1–11
remain separate failures/blockers and are not pooled.

## Frozen causal rules

- On each closed 4h bar, rank each available symbol's 42-bar (seven-day) return, strongest rank 1. Require >=8 ranks.
  Breadth is the same-time fraction with positive seven-day return. All inputs end on the decision bar.
- Long cell: bottom 20% rank, breadth <=35%, RSI(2) crosses above 30, bullish candle. Short mirrors: top 20%, breadth
  >=65%, RSI(2) crosses below 70, bearish candle.
- Candle volume must be >=80% of its preceding 42-bar median. The 1.5 ATR stop must be >=5x the full 5 bp taker + 2 bp
  adverse-slippage round trip. Entry is the next 4h open.
- Exit when rank reaches the middle half/breadth normalizes, or frozen management: 1.5 ATR stop, half at +1R and
  breakeven, +2R final target, 18-bar cap. No pyramiding; same-decision-bar re-entry locked.
- Funding uses actual checksum-verified Binance events, signed by side, with the last verified 1h mark close available at
  the event. Fees and adverse slippage apply to every fill. One primary rule; no grid or post-result symbol selection.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. A side immediately fails on mean net R <=0,
PF <=1.05, non-positive pre-cost gross expectancy, <60 closes, <8 symbols, max drawdown >15R, or fewer than 15 closes in
either reported BTC-above/below-EMA200 regime. A development survivor runs unchanged on:

1. `2025-04-01..2025-09-30`
2. `2025-10-01..2026-03-31`
3. `2026-04-01..2026-09-28` (exclusive)

Final gate per side: aggregate mean net R >0, PF >1.05, >=120 closes over >=8 symbols, >=2/3 positive folds, median >=4
closes/month, max drawdown <=15R, neither regime aggregate below -0.05 mean R, and no symbol >35% of absolute contribution.
The sealed post-2026-09-28 holdout stays closed. Multiplicity: two cells. A failure is parked without rescue.

ROADMAP FIT: low-turnover cross-sectional diversifier rather than another trend/breakout variation.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet, parameter rescue and post-result symbol pruning.

## Run command

```powershell
python tools/research/run_rank_breadth_reversal_candidate.py --cache <4H_CACHE> `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z `
  --tier CORE-20 --side long --out <OUT>\rbr-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to the frozen folds.
