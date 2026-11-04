# Candidate 119 / Correlation Trend Indicator preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `cti_trend_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–118 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Correlation Trend Indicator, CTI, rolling price-
time Pearson correlation, and equivalent normalized trend-fit signals. No prior candidate implements this statistic. C114
uses a rolling regression endpoint residual, while C119 measures normalized direction and goodness of the entire linear fit;
regression/trend correlation is disclosed. One conventional long cell only; no window or threshold grid.

## Frozen causal rule and costs

On completed 1h bars calculate Pearson correlation between the trailing 20 closes and ascending coordinates 0..19. Enter
at the next open only when CTI crosses above +0.5 and close is above EMA20. Exit when CTI is at/below zero, after 24 bars,
or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs
are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Constant/missing windows cannot
qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 119 raises the
numbered family to 119; any later promotion must use family-wise alpha <=`0.05/119` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF price-time alignment family.  
DEFERRED: short cell, alternate windows/thresholds/filters, folds, holdout, Stage B and activation.
