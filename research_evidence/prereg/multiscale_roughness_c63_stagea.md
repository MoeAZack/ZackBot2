# Candidate 63 / multiscale roughness reversion preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `multiscale_roughness_reversion_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–62 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Hurst/fractal exponents, rescaled
range, multiscale variance scaling, anti-persistence, roughness and Lo-MacKinlay variance ratios. No prior candidate
uses a multi-horizon return-variance scaling exponent. C40 estimates only lag-1 linear autocorrelation, C42 discards
magnitudes into sign entropy, C44 estimates a discrete bull-state transition, and C61 compares intrabar high-low
variance with close variance at the same horizon. These mean-reversion/dependence correlations are disclosed, but C63
tests temporal scaling between 1h and 24h returns. C62 supplies no parameter or rule. One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, calculate log close returns over one hour and 24 hours. Over exactly 168 completed
observations at each scale, estimate variances and calculate `H = 0.5 * log(var_24h / var_1h) / log(24)`. Enter long at
the next open only when H is at most 0.40, the decision-hour return is negative, and close is below EMA20. Exit when H
is at least 0.50, close reaches EMA20, after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must
be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus
checksum-verified actual signed funding. Missing or zero-variance history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 63 increases the numbered family to 63;
any later promotion must use family-wise alpha <=`0.05/63` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF multiscale/fractal-scaling family.  
DEFERRED: short/continuation cells, alternative scales/windows/thresholds, folds, holdout, Stage B and activation.
