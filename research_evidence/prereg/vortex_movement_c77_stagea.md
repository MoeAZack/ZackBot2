# Candidate 77 / Vortex directional-range movement preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `vortex_movement_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–76 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for the Vortex Indicator, Vortex movement
and VI+/VI- rules. No prior candidate uses this statistic. C41 measures range acceleration, C47/C48 single outside-bar
geometry and C76 extreme-event recency. Existing trend filters disclose generic directional correlation but none sums
cross-bar high-to-prior-low versus low-to-prior-high travel normalized by true range. One long cell; no grid.

## Frozen causal rule and costs

For each completed 1h bar, positive Vortex movement is `abs(high_t-low_{t-1})`, negative movement is
`abs(low_t-high_{t-1})`, and true range uses the completed prior close. Over exactly 14 completed bars, VI+ and VI- are
their respective movement sums divided by summed true range. Enter long at the next open only when VI+ minus VI- is
>=0.20, the decision-hour return is positive and close is above EMA20. Exit when the oscillator is <=0 or close reaches/
breaches EMA20, after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14%
taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual
signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 77 increases
the numbered family to 77; any later promotion must use family-wise alpha <=`0.05/77` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF cross-range movement family.  
DEFERRED: short cells, other windows/thresholds, folds, holdout, Stage B and activation.
