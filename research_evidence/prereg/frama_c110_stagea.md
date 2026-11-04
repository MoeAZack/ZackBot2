# Candidate 110 / Fractal Adaptive Moving Average crossover preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `frama_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–109 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for FRAMA, fractal adaptive moving averages and
path-dimension-driven smoothing. No prior candidate implements this statistic. Ordinary moving-average correlation is
disclosed, but C110 changes its recursive alpha from the estimated dimension of two half-window high-low paths relative to
the whole range. One conventional long cell only; no window, alpha, filter, or management grid.

## Frozen causal rule and costs

On each completed 1h bar use the trailing 16 bars. Compute N1 and N2 as each 8-bar half's high-low range divided by 8,
and N3 as the full 16-bar high-low range divided by 16. Dimension is `ln((N1+N2)/N3)/ln(2)` and recursive alpha is
`clip(exp(-4.6*(dimension-1)),0.01,1)`. FRAMA is `alpha*close+(1-alpha)*prior_FRAMA`. Enter at the next open only when
close crosses above FRAMA and is above EMA20. Exit when close is at/below FRAMA, after 24 bars, or earlier at the 2 ATR
stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp
adverse slippage per fill plus checksum-verified actual signed funding. Missing warm-up cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 110 raises the
numbered family to 110; any later promotion must use family-wise alpha <=`0.05/110` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF fractal-dimension adaptive-trend family.  
DEFERRED: short cell, alternate windows/alpha bounds/filters, folds, holdout, Stage B and activation.
