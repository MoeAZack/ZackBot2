# Candidate 62 / CUSUM negative-drift change detection preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `cusum_downshift_short_1h`; 1h short only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–61 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for CUSUM, cumulative-sum controls,
Page-Hinkley, sequential drift/change detection and structural breaks. No prior candidate uses a resettable sequential
change alarm. Momentum/trend shorts, C40 signed-return autocorrelation and C60 volatility clustering share generic
downside exposure, but they use fixed-window levels or dependence statistics; C62 instead accumulates standardized
negative innovations until an explicit sequential alarm and resets after each alarm. C61's no-trade result is final and
supplies no threshold or rule. One short cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, divide its close return by the standard deviation of the preceding 48 completed
hourly returns (the volatility estimate is lagged one bar). Update a lower one-sided CUSUM as
`min(0, prior_cusum + standardized_return + 0.10)`. Emit an alarm and reset the accumulator to zero when it reaches
`-4.0`; enter short at the next open. Exit when close reaches EMA20, after 24 completed bars, or earlier at the 2 ATR
stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp
adverse slippage per fill plus checksum-verified actual signed funding. Missing history resets and cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 62 increases the numbered family to 62;
any later promotion must use family-wise alpha <=`0.05/62` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF sequential change-detection family.  
DEFERRED: positive/long alarm, alternative references/windows/thresholds, folds, holdout, Stage B and activation.
