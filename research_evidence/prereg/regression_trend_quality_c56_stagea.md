# Candidate 56 / regression trend-quality preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `regression_trend_quality_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–55 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for rolling price regression, log-price
slope, trend t-statistic, linear-trend significance and regression trend quality. No tested strategy, packet or result
uses the t-statistic of a rolling log-price OLS slope. EMA/momentum cells use endpoint or moving-average direction;
C40 uses lag-1 return autocorrelation; C42 uses sign entropy; RAS uses path efficiency only as a regime selector. C56
instead requires the entire 48-hour log-price path to support a statistically coherent positive slope. Trend-beta
correlation is disclosed, but this regression-quality mechanism is materially distinct. It uses no calendar, volume,
BTC-relative, wick, channel, breakout or oscillator input. One long cell; no window/t-stat/management grid.

## Frozen causal rule and costs

At each completed 1h decision bar `t`, regress log close on integer time `0..47` over exactly the 48 closes ending at
`t`, with intercept. Compute the conventional OLS slope t-statistic using 46 residual degrees of freedom. Enter long at
the next open only when the slope t-statistic is >=+3.0. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage
round-trip cost. Exit when the slope t-statistic is <=0, after 24 completed bars, or earlier at the 2 ATR stop/2R target.
Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing/non-finite
history cannot qualify. The t-statistic is a trend-quality filter, not a calibrated probability of profit.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 56 increases the numbered family to 56;
any later promotion must use family-wise alpha <=`0.05/56` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF statistical trend-quality family.  
DEFERRED: short cell, alternative windows/t-statistics, folds, holdout, Stage B and activation.
