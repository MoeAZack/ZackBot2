# Candidate 79 / Choppiness path-efficiency preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `choppiness_persistence_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–78 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Choppiness Index and logarithmic
true-range/path-containment rules. No prior candidate uses this statistic. C56 tests regression slope quality, C63
variance scaling and C75 ordinal turning points; the existing path-efficiency regime selector is not this tested rule.
They disclose trend correlation but do not compare total traversed true range with the enclosing high-low span through
the normalized logarithmic index. One long cell; no grid.

## Frozen causal rule and costs

At every completed 1h decision bar, use exactly 14 completed bars ending at `t`. Choppiness is
`100 * log10(sum(true_range)/(max(high)-min(low))) / log10(14)`. Enter long at the next open only when Choppiness is
<=38.2, the completed 24h return and decision-hour return are positive, and close is above EMA20. Exit when Choppiness
is >=61.8 or the 24h return is non-positive, after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR
stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill
plus checksum-verified actual signed funding. Zero enclosing range cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 79 increases
the numbered family to 79; any later promotion must use family-wise alpha <=`0.05/79` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF range-efficiency family.  
DEFERRED: short cells, other windows/thresholds, folds, holdout, Stage B and activation.
