# Candidate 123 / McGinley Dynamic crossover preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `mcginley_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–122 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for McGinley Dynamic, nonlinear price-ratio adaptive
averages, and fourth-power lag adjustment. No prior candidate implements this statistic. Adaptive-average correlation with
C110 FRAMA and C117 KAMA is disclosed, but C123 changes speed asymmetrically through `(price/prior)^4` rather than fractal
dimension or path efficiency. One conventional long cell only; no period/constant/management grid.

## Frozen causal rule and costs

On completed 1h closes initialize the dynamic at the first close and recursively update it as
`prior+(close-prior)/(0.6*14*(close/prior)^4)`. Enter at the next open only when close crosses above this dynamic and is
above EMA20. Exit when close is at/below the dynamic, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop
must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus
checksum-verified actual signed funding. Nonpositive/invalid state cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 123 raises the
numbered family to 123; any later promotion must use family-wise alpha <=`0.05/123` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF nonlinear ratio-adaptive trend family.  
DEFERRED: short cell, alternate periods/constants/filters, folds, holdout, Stage B and activation.
