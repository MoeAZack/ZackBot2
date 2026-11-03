# Candidate 105 / Heikin-Ashi state-flip preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `heikin_ashi_flip_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–104 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Heikin-Ashi, Heiken-Ashi, synthetic/transformed
candles, and recursive candle-state signals. No prior candidate implements this statistic. Unlike ordinary candle-pattern
families, C105 recursively transforms prior transformed open/close state; unlike EMA oscillators, its state includes all
four current OHLC values. Trend correlation is disclosed. One long cell only; no threshold or management grid.

## Frozen causal rule and costs

On completed 1h bars, Heikin-Ashi close is `(open+high+low+close)/4`; its open is seeded `(open+close)/2` and thereafter
equals the mean of the preceding transformed open and close. Enter at the next open when the completed transformed candle
changes from bearish/non-bullish to bullish and ordinary close is above EMA20. Exit when transformed state is non-bullish,
after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip
cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Warm-up cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 105 raises the
numbered family to 105; any later promotion must use family-wise alpha <=`0.05/105` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF recursively transformed-candle family.  
DEFERRED: short cell, alternate filters/management, folds, holdout, Stage B and activation.
