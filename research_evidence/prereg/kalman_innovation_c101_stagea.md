# Candidate 101 / local-level Kalman innovation-recovery preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `kalman_innovation_recovery_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–100 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Kalman filters, state-space local-level
models, filter covariance and standardized innovations. No prior candidate implements this mechanism. C92 uses a fixed
window median/MAD price displacement, C65 estimates OU half-life, and C62 accumulates return shocks in CUSUM. C101
instead updates a causal latent log-price level and uncertainty after every bar, then standardizes each new observation
by predicted covariance. Mean-reversion correlation is disclosed. One long cell; no variance/threshold grid.

## Frozen causal rule and costs

Run a scalar local-level Kalman filter sequentially on completed hourly log closes: state transition is identity,
process variance `0.000025`, observation variance `0.0004`, initial state is the first log close, and initial variance is
the observation variance. Before updating each bar, score its innovation by the square root of predicted state plus
observation variance. Enter long at the next open only when the prior score was <=-2.0, the current score is >-1.0, the
current return is positive, and close remains below EMA20. Exit when score reaches zero, after eight bars, or earlier at
the 1.5 ATR stop/1.5R target. A 1.5 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5
bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 101 raises
the numbered family to 101; any later promotion must use family-wise alpha <=`0.05/101` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF causal state-space dislocation family.  
DEFERRED: short cell, alternate covariance/threshold/filter rules, folds, holdout, Stage B and activation.
