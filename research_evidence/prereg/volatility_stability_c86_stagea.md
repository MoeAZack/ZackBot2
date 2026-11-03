# Candidate 86 / Volatility-of-volatility stability preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `volatility_stability_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–85 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for volatility-of-volatility, vol-of-vol,
realized-volatility stability and volatility coefficient-of-variation rules. No prior candidate uses this statistic.
C41 measures a range-level acceleration and C60 measures squared-return serial dependence. Those volatility families
are related disclosures but neither measures dispersion of a rolling realized-volatility series relative to its mean.
One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, compute the population standard deviation of the last six completed log returns.
Over the last 48 completed values of that six-hour realized-volatility series, compute population standard deviation
divided by mean. Enter long at the next open only when this volatility-of-volatility coefficient is <=0.50, the decision-
hour and completed 24h returns are positive, and close is above EMA20. Exit when the coefficient is >=0.80 or the 24h
return is non-positive, after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x
modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-
verified actual signed funding. Zero mean realized volatility cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 86 increases
the numbered family to 86; any later promotion must use family-wise alpha <=`0.05/86` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF volatility-stability family.  
DEFERRED: short cells, other horizons/thresholds, folds, holdout, Stage B and activation.
