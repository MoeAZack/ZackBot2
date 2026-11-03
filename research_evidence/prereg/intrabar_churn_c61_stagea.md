# Candidate 61 / intrabar-churn reversion preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `intrabar_churn_reversion_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–60 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Parkinson/range-based variance,
intrabar variance, range-to-close variance ratios, microstructure noise and intrabar churn. No prior candidate uses the
rolling ratio of high-low variance to close variance. ATR shock snapback is a one-bar event, C41 compares short/long
close-volatility levels, C55 uses directional wick imbalance, and transient-jump dislocation uses close-return jumps.
Those volatility/range correlations and generic long mean-reversion exposure are disclosed, but C61 tests persistent
intrabar auction churn relative to net close displacement. C60 failure supplies no parameter or rule. One long cell;
no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, estimate Parkinson variance as `log(high/low)^2 / (4 log(2))` and close variance as
`log(close/previous_close)^2`. Over exactly 168 completed bars, divide mean Parkinson variance by mean close variance.
Enter long at the next open only when this ratio is at least 2.0, the decision-hour close return is negative, and close
is below EMA20. Exit when the ratio is at most 1.0, close reaches EMA20, after 24 completed bars, or earlier at the 2 ATR
stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp
adverse slippage per fill plus checksum-verified actual signed funding. Missing history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 61 increases the numbered family to 61;
any later promotion must use family-wise alpha <=`0.05/61` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF range-variance/microstructure-noise family.  
DEFERRED: short cell, alternative windows/ratios/estimators, folds, holdout, Stage B and activation.
