# Candidate 117 / Kaufman Adaptive Moving Average crossover preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `kama_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–116 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Kaufman Adaptive Moving Average, KAMA, and
efficiency-ratio-driven adaptive smoothers. No prior candidate implements this statistic. C111 thresholds a similar path-
efficiency ratio directly, while C110 FRAMA adapts from high-low fractal dimension; C117 uniquely maps efficiency between
fast/slow constants, squares it, and recursively smooths price. Correlation is disclosed. One conventional cell; no grid.

## Frozen causal rule and costs

On completed 1h closes calculate 10-bar efficiency as absolute endpoint change divided by summed absolute close changes.
Map it between fast constant `2/(2+1)` and slow constant `2/(30+1)`, square the result, then update KAMA recursively as
`prior+alpha*(close-prior)`. Enter at the next open only when close crosses above KAMA and is above EMA20. Exit when close
is at/below KAMA, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-
slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 117 raises the
numbered family to 117; any later promotion must use family-wise alpha <=`0.05/117` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF efficiency-adaptive trend family.  
DEFERRED: short cell, alternate windows/speeds/filters, folds, holdout, Stage B and activation.
