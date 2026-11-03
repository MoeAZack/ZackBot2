# Candidate 68 / round-number breakout preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `round_number_breakout_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–67 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for round numbers, psychological price
levels/barriers, decimal/price clustering and price lattices. No prior candidate uses an exogenous decimal grid. C45
uses repeated endogenous highs; Donchian/daily-range and inside-bar candidates use recent price extrema. Their generic
breakout correlation is disclosed, but C68 tests a price-invariant decimal barrier intended to proxy clustered stops
and resting orders. C67 supplies no parameter or rule. One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar derive the decimal step from the prior close as
`10 ** (floor(log10(prior_close)) - 1)`. The upside barrier is the next integer multiple of that step. Enter long at
the next open only when the prior close is strictly below the barrier, the decision close is at or above it, the
decision close is above the prior close, and the completed 24-hour return is positive. Exit when close falls below the
lower grid level surrounding the prior close or the 24-hour return is non-positive, after 24 completed bars, or earlier
at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp
taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Non-positive/missing prices cannot
qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 68 increases the numbered family to 68;
any later promotion must use family-wise alpha <=`0.05/68` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF decimal price-barrier microstructure family.  
DEFERRED: short/reversal cells, alternative grid scales, folds, holdout, Stage B and activation.
