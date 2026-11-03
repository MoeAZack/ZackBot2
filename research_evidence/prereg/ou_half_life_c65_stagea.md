# Candidate 65 / OU half-life reversion preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `ou_half_life_reversion_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–64 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Ornstein-Uhlenbeck/OU processes,
error correction, mean-reversion speed/half-life and price AR(1) rules. No prior candidate conditions entry on an
estimated price error-correction half-life. Keltner/RSI/Williams recovery cells use fixed technical bands, C40 models
return autocorrelation, and C54 models BTC-relative residuals. These generic mean-reversion correlations are disclosed,
but C65 tests a single-asset statistical reversion-speed constraint. C64 supplies no parameter or rule. One long cell;
no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, use exactly 168 completed log closes. Estimate the error-correction coefficient as
rolling `Cov(delta_log_price, lag_log_price) / Var(lag_log_price)` and implied half-life as `-log(2)/beta` when beta is
negative. Calculate the current log-price z-score over the same window. Enter long at the next open only when half-life
is 2 through 24 hours, z-score is at most -2.0, and the decision-hour return is positive. Exit when z-score reaches zero,
beta becomes non-negative, half-life leaves the frozen band, after 24 completed bars, or earlier at the 2 ATR stop/2R
target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse
slippage per fill plus checksum-verified actual signed funding. Missing/zero-variance history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 65 increases the numbered family to 65;
any later promotion must use family-wise alpha <=`0.05/65` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF error-correction/mean-reversion-speed family.  
DEFERRED: short cell, alternative bands/windows/z-scores, folds, holdout, Stage B and activation.
