# Candidate 114 / Chande Forecast Oscillator preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `cfo_zero_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–113 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Chande Forecast Oscillator, CFO, rolling linear-
regression endpoint residuals, and equivalent forecast oscillators. No prior candidate implements this statistic. C101
Kalman innovation is another forecast-error family, but uses a recursive state-space prior; C114 uses an equal-window OLS
endpoint that includes the completed decision bar. Ulcer Index was rejected before freeze as a direct C83 duplicate. One cell.

## Frozen causal rule and costs

On completed 1h bars fit ordinary least squares to closes at x=0..13 over the trailing 14 bars and evaluate the fitted line
at x=13. CFO is `100*(current close-fitted endpoint)/current close`. Enter at the next open only when CFO crosses above
zero and close is above EMA20. Exit when CFO is non-positive, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR
stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill
plus checksum-verified actual signed funding. Missing warm-up cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 114 raises the
numbered family to 114; any later promotion must use family-wise alpha <=`0.05/114` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF rolling-regression forecast-residual family.  
DEFERRED: short cell, alternate windows/thresholds/filters, folds, holdout, Stage B and activation.
