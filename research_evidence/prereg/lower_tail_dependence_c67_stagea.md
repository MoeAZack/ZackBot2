# Candidate 67 / lower-tail dependence preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `lower_tail_dependence_short_1h`; 1h short only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–66 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for tail dependence, lower-tail
dependence, copulas, joint/co-exceedances, downside dependence and tail beta. No prior candidate conditions entry on
empirical conditional lower-tail co-exceedance. C54 models linear BTC beta and a one-hour residual; C21 models BTC
order-flow diffusion; C24 uses cross-sectional rotation. Their cross-asset correlation is disclosed, but C67 tests
nonlinear crash-state coupling and follows a contemporaneous BTC deleveraging shock. C66 supplies no parameter or rule.
One short cell; no grid.

## Frozen causal rule and costs

At every completed 1h decision bar calculate target and BTC close returns. From exactly 336 observations ending at
`t-1`, obtain each series' empirical 10th percentile and the prior-only conditional frequency
`P(target <= target_q10 | BTC <= BTC_q10)`. Require at least 20 prior BTC lower-tail observations. Enter short at the
next open only when dependence is at least 0.40, the current completed BTC return is at or below its frozen prior 10th
percentile, and the current target return is negative. Exit when dependence is at most 0.20 or the BTC return is
non-negative, after 12 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14%
taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual
signed funding. Missing history or zero qualifying BTC observations cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 67 increases the numbered family to 67;
any later promotion must use family-wise alpha <=`0.05/67` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF nonlinear cross-asset tail-dependence family.  
DEFERRED: long/reversal cell, alternative quantiles/windows/thresholds, folds, holdout, Stage B and activation.
