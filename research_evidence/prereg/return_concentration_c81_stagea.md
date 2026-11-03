# Candidate 81 / Temporal return-concentration preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `return_concentration_persistence_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–80 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for absolute-return concentration,
return Herfindahl/HHI and effective-move-count rules. No prior candidate uses this statistic. C71 measures concentration
of quote volume, C73 measures the level of a trimmed return, and C80 measures positive-jump arrival intensity. Those
families disclose generic path/impulse correlation but neither measures how total absolute price movement is allocated
through time. One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, take the last 24 completed absolute log returns. Define return concentration as
`sum(abs_return^2) / sum(abs_return)^2`, the Herfindahl index of their shares. Enter long at the next open only when
concentration is <=0.08, the decision-hour log return and completed 24h return are positive, and close is above EMA20.
Exit when concentration is >=0.15 or the 24h return is non-positive, after 24 completed bars, or earlier at the 2 ATR
stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp
adverse slippage per fill plus checksum-verified actual signed funding. A zero absolute-return sum cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 81 increases
the numbered family to 81; any later promotion must use family-wise alpha <=`0.05/81` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF temporal return-concentration family.  
DEFERRED: short cells, other windows/thresholds, folds, holdout, Stage B and activation.
