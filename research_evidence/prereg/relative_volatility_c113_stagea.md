# Candidate 113 / Relative Volatility Index preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `relative_volatility_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–112 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Relative Volatility Index, RVI directional
volatility, and up/down-assigned standard-deviation ratios. No prior candidate implements this statistic. C98 Relative Vigor
shares an abbreviation but measures normalized signed candle bodies; C113 assigns rolling close dispersion by close direction.
Generic volatility/momentum correlation is disclosed. One conventional long cell only; no parameter or threshold grid.

## Frozen causal rule and costs

On completed 1h bars compute the population standard deviation of close over 10 bars. Assign that dispersion to the up
series only when close advanced, or the down series only when close declined, then Wilder-smooth both over 14 bars. RVI is
`100*up/(up+down)`. Enter at the next open only when RVI crosses above 50 and close is above EMA20. Exit when RVI is at or
below 50, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage
round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 113 raises the
numbered family to 113; any later promotion must use family-wise alpha <=`0.05/113` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF directional-volatility balance family.  
DEFERRED: short cell, alternate windows/thresholds/filters, folds, holdout, Stage B and activation.
