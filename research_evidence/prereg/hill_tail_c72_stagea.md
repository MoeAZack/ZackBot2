# Candidate 72 / Hill downside-tail recovery preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `hill_tail_recovery_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–71 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Hill estimators, Pareto/extreme-value
tail indices and tail exponents. No prior candidate estimates a downside-tail exponent. C16 realized skew, C51 realized
kurtosis, C57 downside semivariance and C66 jump variation are related distributional families, but measure moments or
path decomposition rather than the scaling law of ordered extremes. C71 supplies no parameter or rule. One long cell;
no grid.

## Frozen causal rule and costs

At every completed 1h decision bar, compute exactly 168 completed log returns ending at `t`. Select the negative-return
magnitudes, order them descending, and use the largest `k=12` observations with the 13th as threshold. The causal Hill
tail exponent is `alpha = 12 / sum(log(x_i / x_13))`; at least 13 strictly positive losses and a positive denominator
are mandatory. Enter long at the next open only when `alpha <= 3.0`, the decision-hour return is positive and the close
remains below EMA20. Exit when `alpha >= 5.0` or close reaches EMA20, after 24 completed bars, or earlier at the 2 ATR
stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp
adverse slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 72 increases
the numbered family to 72; any later promotion must use family-wise alpha <=`0.05/72` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF extreme-value family.  
DEFERRED: short cells, alternative windows/k/thresholds/estimators, folds, holdout, Stage B and activation.
