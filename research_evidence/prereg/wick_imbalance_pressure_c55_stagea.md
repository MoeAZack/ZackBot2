# Candidate 55 / wick-imbalance auction pressure preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `wick_imbalance_pressure_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–54 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for wick imbalance, upper/lower wick
pressure, close-location value, candle rejection and auction pressure. No tested strategy, packet or result aggregates
normalized lower-minus-upper wick asymmetry across a fixed multi-hour window. Liquidity-sweep rejection requires one
bar to breach a prior level and close back inside; outside bars engulf a predecessor; daily-range participation uses one
bar's top-quartile close plus volume and trend. C55 instead measures persistent OHLC auction rejection without volume,
calendar, BTC, trend, channel, oscillator or breakout selection. Correlation with single-candle rejection patterns is
disclosed, but the repeated auction-pressure mechanism is materially distinct. One long cell; no window/threshold grid.

## Frozen causal rule and costs

For each completed 1h candle, define upper wick as `high-max(open,close)`, lower wick as `min(open,close)-low`, and
normalized wick imbalance as `(lower-upper)/(high-low)`. At completed decision bar `t`, average exactly the latest 12
imbalances including `t`. Enter long at the next open only when the mean is >=+0.20 and `close[t] > open[t]`. A 1.5 ATR
stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Exit when pressure is <=0, after eight completed
bars, or earlier at the 1.5 ATR stop/1.5R target. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-
verified actual signed funding. Zero-range or missing-history windows cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 55 increases the numbered family to 55;
any later promotion must use family-wise alpha <=`0.05/55` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF OHLC auction-pressure family.  
DEFERRED: short cell, alternative windows/thresholds, trend/volume filters, folds, holdout, Stage B and activation.
