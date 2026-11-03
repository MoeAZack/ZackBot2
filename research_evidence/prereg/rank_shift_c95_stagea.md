# Candidate 95 / Mann-Whitney return rank-shift preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `return_rank_shift_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–94 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Mann-Whitney, Wilcoxon rank-sum,
return stochastic dominance, return-distribution shifts and pairwise sample dominance. No prior candidate uses this
statistic. C39 and legacy momentum rules compare parametric price trends, while C88 orders prices within one path. C95
instead compares every recent return with every return in a disjoint prior sample, ignoring magnitude beyond order.
Those trend/rank families are disclosed but materially distinct. One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, use the most recent 24 completed log returns as the recent sample and the immediately
preceding disjoint 72 returns as baseline. Rank-shift dominance is the fraction of all 24x72 recent/baseline pairs where
the recent return is greater, with ties worth one-half. Enter long at the next open only when dominance is >=0.60, the
current completed-hour and completed 24h returns are positive, and close is above EMA20. Exit when dominance is <=0.50
or the 24h return is non-positive, after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be
>=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus
checksum-verified actual signed funding. Missing history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 95 increases
the numbered family to 95; any later promotion must use family-wise alpha <=`0.05/95` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF nonparametric distribution-shift family.  
DEFERRED: short/reversal cells, other sample horizons/thresholds/tests, folds, holdout, Stage B and activation.
