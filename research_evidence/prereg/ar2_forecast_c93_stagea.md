# Candidate 93 / causal AR(2) return-forecast preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `ar2_forecast_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–92 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for AR(2), rolling autoregressive
forecasts, two-lag regression and standardized next-return forecasts. No prior candidate uses this model. C40 filters on
a descriptive lag-one return correlation and C65 fits one-lag mean reversion to price deviations; C93 instead estimates
an intercept and both first- and second-lag coefficients jointly to form a conditional next-return forecast. Those
serial-dependence families are disclosed but materially distinct. One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, use exactly 168 completed log returns. Fit by ordinary least squares
`r_t = intercept + b1*r_(t-1) + b2*r_(t-2)` over the available triples, then forecast the next return from the two most
recent completed returns. Divide that forecast by the population standard deviation of the regression response returns.
Zero variance or an unsolved fit cannot qualify. Enter long at the next open only when this standardized forecast is
>=0.25, the current completed-hour and completed 24h returns are positive, and close is above EMA20. Exit when the
forecast is <=0 or the 24h return is non-positive, after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2
ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per
fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 93 increases
the numbered family to 93; any later promotion must use family-wise alpha <=`0.05/93` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF two-lag conditional-forecast family.  
DEFERRED: short cells, other orders/windows/thresholds/regularization, folds, holdout, Stage B and activation.
