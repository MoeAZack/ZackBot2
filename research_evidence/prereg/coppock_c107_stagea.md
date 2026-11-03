# Candidate 107 / Coppock-curve zero-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `coppock_zero_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–106 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Coppock curves, dual-rate-of-change sums and
weighted-moving-average ROC signals. No prior candidate implements this statistic. C104 TRIX transforms the one-bar rate
of change of a triple EMA, whereas C107 weights the sum of two endpoint returns; generic momentum correlation is disclosed.
One conventional long cell only; no lookback, threshold, filter, or management grid.

## Frozen causal rule and costs

On completed 1h bars, calculate percentage rate of change over 14 and 11 bars, sum them, then apply a 10-bar linearly
weighted moving average with newest observation weight 10. Enter at the next open only when the Coppock curve crosses
above zero and close is above EMA20. Exit when the curve is non-positive, after 24 bars, or earlier at the 2 ATR stop/2R
target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse
slippage per fill plus checksum-verified actual signed funding. Missing warm-up cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 107 raises the
numbered family to 107; any later promotion must use family-wise alpha <=`0.05/107` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF dual-horizon rate-of-change recovery family.  
DEFERRED: short cell, alternate lookbacks/thresholds/filters, folds, holdout, Stage B and activation.
