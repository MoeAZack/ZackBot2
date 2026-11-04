# Candidate 120 / Hurst rescaled-range persistence preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `hurst_persistence_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–119 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Hurst exponents, rescaled-range/R-S analysis,
and multi-scale persistence estimates. No prior candidate implements this statistic. Generic persistence/trend correlation is
disclosed, but C120 estimates the scaling exponent of demeaned cumulative-return ranges across block sizes rather than a
linear fit, endpoint return, transition count, or moving average. One conventional long cell only; no estimation grid.

## Frozen causal rule and costs

On each completed 1h bar use 64 closes and their 63 returns. At block sizes 8, 16, and 32, split returns into complete
non-overlapping blocks, demean each, calculate cumulative-deviation range divided by population standard deviation, and
average R/S within scale. Hurst is the OLS slope of log average R/S on log block size. Enter at the next open only when H
crosses above 0.60, the 24-bar close return is positive, and close is above EMA20. Exit when H<0.50 or 24-bar return<=0,
after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled round-trip cost. Costs are 5 bp
taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Invalid/constant blocks cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 120 raises the
numbered family to 120; any later promotion must use family-wise alpha <=`0.05/120` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF multi-scale persistence family.  
DEFERRED: short cell, alternate windows/scales/thresholds/filters, folds, holdout, Stage B and activation.
