# Candidate 74 / Mass Index range-bulge recovery preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `mass_index_recovery_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–73 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Mass Index, double-smoothed range
ratios and range bulges. No prior candidate uses this statistic. C41 compares six-hour true range with a prior baseline,
C61 compares Parkinson with close variance, and several pattern candidates use one-bar close location. They disclose
generic OHLC/range correlation but do not measure persistent range expansion through the summed single/double EMA ratio.
One long cell; no grid.

## Frozen causal rule and costs

For each completed 1h bar, compute high minus low, its 9-period EMA, a second 9-period EMA of that series, then sum the
single/double EMA ratio over exactly 25 completed bars. Enter long at the next open only when this Mass Index is >=27,
the decision-hour return is positive and close remains below EMA20. Exit when Mass Index <=25 or close reaches EMA20,
after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14%
taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual
signed funding. Zero-range bars cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 74 increases
the numbered family to 74; any later promotion must use family-wise alpha <=`0.05/74` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF range-cycle family.  
DEFERRED: short cells, other smoothing/sum/threshold choices, folds, holdout, Stage B and activation.
