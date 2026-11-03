# Candidate 69 / price-recurrence discovery preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `price_recurrence_discovery_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–68 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for recurrence/occupation density, price
revisits, local price density/time and historical inventory density. No prior candidate measures the share of prior
closes near the current price. Donchian/equal-high/round-number breakouts use extrema or an exogenous lattice; C43
measures adjacent candle-range overlap before a boundary break. Their generic continuation correlation is disclosed,
but C69 tests local price occupation without requiring a new high, named barrier or volume input. C68 supplies no
parameter or rule. One long cell; no grid.

## Frozen causal rule and costs

At every completed 1h decision bar count, among exactly 168 prior completed closes ending at `t-1`, the fraction within
0.5 times the current completed ATR14 of the decision close. Enter long at the next open only when this recurrence
density is at most 0.05, the decision-hour return is positive and the completed 24-hour return is positive. Exit when
density is at least 0.20 or the 24-hour return is non-positive, after 24 completed bars, or earlier at the 2 ATR stop/2R
target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse
slippage per fill plus checksum-verified actual signed funding. Missing history or non-positive ATR cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 69 increases the numbered family to 69;
any later promotion must use family-wise alpha <=`0.05/69` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF local price-occupation/recurrence family.  
DEFERRED: short/reversal cell, alternative windows/radii/density thresholds, folds, holdout, Stage B and activation.
