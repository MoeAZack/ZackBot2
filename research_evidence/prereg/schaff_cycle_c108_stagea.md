# Candidate 108 / Schaff Trend Cycle preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `schaff_cycle_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–107 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Schaff Trend Cycle, stochastic-MACD transforms,
and dual-stochastic trend cycles. No prior candidate implements this statistic. MACD and stochastic relatives are disclosed,
but C108 uniquely passes the 23/50 EMA spread through two rolling stochastic normalizations with recursive smoothing.
Mass Index was considered and rejected before freeze as a direct C74 duplicate. One conventional long cell only; no grid.

## Frozen causal rule and costs

On completed 1h bars calculate MACD as EMA23 minus EMA50. Normalize it stochastically over 10 bars to [0,100], smooth
recursively with alpha 0.5, normalize that smoothed series stochastically over 10 bars, and again smooth with alpha 0.5.
Enter at the next open only when this STC crosses above 25 and close is above EMA20. Exit only on a downward crossing of
75, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage
round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 108 raises the
numbered family to 108; any later promotion must use family-wise alpha <=`0.05/108` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF dual-stochastic MACD cycle family.  
DEFERRED: short cell, alternate parameters/thresholds/filters, folds, holdout, Stage B and activation.
