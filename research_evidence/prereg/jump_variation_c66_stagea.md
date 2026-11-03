# Candidate 66 / jump-variation downside preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `jump_variation_downside_short_1h`; 1h short only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–65 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for bipower variation, jump variation,
realized jumps, discontinuous variation, RV-minus-BV and jump share. No prior candidate conditions entry on the
nonparametric RV-minus-bipower decomposition. Transient jump dislocation tests one-bar reversal, C51 tests kurtosis,
C57 compares directional semivariances and C60 tests squared-return autocorrelation. Their generic tail/volatility
correlation is disclosed, but C66 tests the share of path variation attributable to discontinuities and follows its
downside direction. C65 supplies no parameter or rule. One short cell; no grid.

## Frozen causal rule and costs

At every completed 1h decision bar use exactly 168 completed close-to-close log returns. Realized variance is the sum
of squared returns. Bipower variation is `(pi/2) * sum(abs(r[t]) * abs(r[t-1]))`. Jump share is
`max(0, RV-BV)/RV`. Enter short at the next open only when jump share is at least 0.25, the completed 24-hour return is
negative and the current completed log return is negative. Exit when jump share is at most 0.10 or the 24-hour return
is non-negative, after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled
0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified
actual signed funding. Missing/zero-RV history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 66 increases the numbered family to 66;
any later promotion must use family-wise alpha <=`0.05/66` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF nonparametric jump-decomposition family.  
DEFERRED: long/reversal cells, alternative windows/thresholds, folds, holdout, Stage B and activation.
