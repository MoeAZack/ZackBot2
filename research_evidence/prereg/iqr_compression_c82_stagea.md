# Candidate 82 / Return-IQR compression release preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `iqr_compression_breakout_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–81 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for return-IQR, interquartile/quantile
compression and robust volatility-compression breakout rules. No prior candidate uses this statistic. C41 measures
range acceleration, C51 measures realized kurtosis, and C81 measures absolute-return concentration. Those volatility
and distribution families are related disclosures but do not compare the central return spread with its lagged baseline.
One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, compute the interquartile range of the last 24 completed log returns. Divide it by
the median of the prior 96 rolling-IQR values, explicitly excluding the current IQR through a one-bar lag. Enter long at
the next open only when this ratio is <=0.60, the completed decision-hour log return is >=`0.50 * ATR / close`, the
completed 24h return is positive, and close is above EMA20. Exit when the ratio is >=1.0 or the 24h return is non-positive,
after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-
slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 82 increases
the numbered family to 82; any later promotion must use family-wise alpha <=`0.05/82` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF robust return-compression family.  
DEFERRED: short cells, other windows/thresholds/quantiles, folds, holdout, Stage B and activation.
