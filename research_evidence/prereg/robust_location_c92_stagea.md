# Candidate 92 / robust median-MAD location preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `robust_location_reversion_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–91 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for median absolute deviation, robust
z-score, Hampel price filters and robust-location reversion. No prior candidate uses this statistic. C37 uses an
ATR-scaled EMA envelope, C38 an extrema-based oscillator, and C73 a trimmed mean of returns; C92 instead estimates a
rolling robust price location and robust dispersion with median/MAD. Those generic reversion families are disclosed but
materially distinct. One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, use exactly 48 completed log closes. Compute their median and median absolute
deviation, scale MAD by 1.4826, and form `(current_log_close - median)/(1.4826*MAD)`. Zero MAD cannot qualify. Enter long
at the next open only when this robust z-score is <=-2.5, the current completed-hour return is positive, and close remains
below EMA20. Exit when z-score is >=-0.5 or close reaches EMA20, after 24 completed bars, or earlier at the 2 ATR stop/2R
target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse
slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 92 increases
the numbered family to 92; any later promotion must use family-wise alpha <=`0.05/92` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF robust-location/dislocation family.  
DEFERRED: short cells, other windows/z thresholds/robust scales, folds, holdout, Stage B and activation.
