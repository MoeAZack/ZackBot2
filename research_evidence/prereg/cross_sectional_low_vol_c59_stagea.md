# Candidate 59 / cross-sectional low-volatility factor preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `cross_sectional_low_vol_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–58 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for low-volatility factors, realized-
volatility ranks, cross-sectional volatility and defensive factors. No prior candidate ranks the research universe by
trailing realized volatility. C24 ranks risk-adjusted return, rank-breadth reversal ranks 42h return, C41 compares an
asset's own short/long volatility, and the RAS selector uses an asset's own volatility percentile as state. C59 instead
tests the cross-sectional low-volatility anomaly directly. General long-market correlation and fixed-universe survivor
bias are disclosed. C56/C57 near-threshold results and C58 failure supply no parameter or rule. One cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, calculate close-to-close return standard deviation over exactly 168 completed bars
for every available member of the standard research pack and rank ascending. At least eight members must have valid
history. Enter long at the next open when a symbol ranks in the lowest five. Exit when rank exceeds eight, after 24
completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage
round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding.
Ranks use only same-time completed bars and missing history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 59 increases the numbered family to 59;
any later promotion must use family-wise alpha <=`0.05/59` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF cross-sectional defensive factor.  
DEFERRED: short/high-vol cell, alternative windows/rank buckets, folds, holdout, Stage B and activation.
