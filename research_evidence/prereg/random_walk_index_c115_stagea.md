# Candidate 115 / Random Walk Index preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `random_walk_index_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–114 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Random Walk Index, RWI, and directional
displacement normalized by ATR times square-root horizon. No prior candidate implements this statistic. Gopalakrishnan Range
Index was rejected before freeze as too close to C79 logarithmic Choppiness. C115 compares directional high/low travel with
a random-walk scale across horizons; generic ATR/trend correlation is disclosed. One conventional long cell only; no grid.

## Frozen causal rule and costs

On completed 1h bars, for each n=2..14 compute RWI-high as `(current high-low[n-1])/(mean true range over n*sqrt(n))`
and RWI-low symmetrically as `(high[n-1]-current low)/scale`; retain each side's maximum across n. Enter at the next open
only when RWI-high crosses above 1, exceeds RWI-low, and close is above EMA20. Exit when RWI-high is <=1 or <=RWI-low,
after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip
cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 115 raises the
numbered family to 115; any later promotion must use family-wise alpha <=`0.05/115` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF ATR-scaled directional random-walk family.  
DEFERRED: short cell, alternate horizons/thresholds/filters, folds, holdout, Stage B and activation.
