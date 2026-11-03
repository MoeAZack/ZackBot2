# Candidate 73 / trimmed-return dislocation recovery preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `trimmed_return_recovery_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–72 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for trimmed/winsorized return location,
robust-location dislocation and tail-driven mean/trimmed-mean divergence. No prior candidate uses this statistic. C31
fades one-bar return-per-volume shocks, C51 measures kurtosis, C66 measures jump-variation share and C72 estimates a
downside-tail exponent. They disclose generic tail/shock correlation but do not test whether a negative multi-hour
location is entirely caused by extremes while the distribution's center stays positive. One long cell; no grid.

## Frozen causal rule and costs

At every completed 1h decision bar, take exactly 24 completed log returns ending at `t`, sort them, discard the four
smallest and four largest, and average the central 16. Enter long at the next open only when the untrimmed 24h log-return
sum is <=-3%, the symmetric trimmed mean is >=+0.01% per hour, the decision-hour return is positive and close remains
below EMA20. Exit when the untrimmed sum reaches zero or the trimmed mean reaches zero, after 24 completed bars, or
earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs
are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 73 increases
the numbered family to 73; any later promotion must use family-wise alpha <=`0.05/73` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF robust-location family.  
DEFERRED: short cells, other windows/trim fractions/thresholds, folds, holdout, Stage B and activation.
