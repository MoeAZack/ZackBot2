# Candidate 122 / Projection Oscillator preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `projection_oscillator_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–121 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Projection Oscillator/Bands and projected high-
low regression channels. No prior candidate implements this statistic. Aroon was rejected before freeze as a direct C76
duplicate. Price-location and regression correlation with C106/C114 is disclosed, but C122 independently detrends high and
low envelopes and locates close between their current projections. One conventional long cell only; no parameter grid.

## Frozen causal rule and costs

On completed 1h bars, fit separate OLS slopes to trailing 14 highs and lows at x=0..13. The projected upper band at x=13
is the maximum high residual plus its fitted slope at 13; the lower band is the minimum low residual plus its slope at 13.
Projection Oscillator is `100*(close-lower)/(upper-lower)`. Enter at the next open only when it crosses above 50 and close
is above EMA20. Exit at/below 50, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled
0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 122 raises the
numbered family to 122; any later promotion must use family-wise alpha <=`0.05/122` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF projected-channel location family.  
DEFERRED: short cell, alternate windows/thresholds/filters, folds, holdout, Stage B and activation.
