# Candidate 89 / nonlinear time-irreversibility preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `time_reversal_asymmetry_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–88 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for time-reversal asymmetry, time
irreversibility, third-order temporal moments and nonlinear return ordering. There were no matches. C40 measures linear
lag-1 return autocorrelation, C60 squared-return autocorrelation, C64 return/next-volatility leverage effect, and C88
pairwise ordinal price monotonicity. C89 instead measures whether the order of adjacent return magnitudes has a nonzero
third-order signed moment. Those temporal-dependence families are disclosed but materially distinct. One long cell; no
grid.

## Frozen causal rule and costs

At each completed 1h decision bar, calculate log returns and, over the last 48 adjacent completed-return pairs, the mean
of `r_t^2*r_(t-1) - r_t*r_(t-1)^2`, divided by the cube of the population standard deviation of the same 48 current
returns. Enter long at the next open only when this statistic is >=0.10, the current completed-hour log return and
completed 24h return are positive, and close is above EMA20. Exit when the statistic is <=0 or the 24h return is
non-positive, after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14%
taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual
signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 89 increases
the numbered family to 89; any later promotion must use family-wise alpha <=`0.05/89` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF nonlinear temporal-asymmetry family.  
DEFERRED: short cells, other windows/thresholds/moments, folds, holdout, Stage B and activation.
