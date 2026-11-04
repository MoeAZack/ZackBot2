# Candidate 129 / Hull Moving Average crossover preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `hull_ma_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–128 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Hull Moving Average, Hull MA, HMA crossover,
weighted half/full-window difference and square-root smoothing. No prior candidate implements this statistic. Moving-average
correlation with C110 FRAMA, C117 KAMA and C123 McGinley is disclosed; C129 is a finite impulse response made from
`2*WMA(n/2)-WMA(n)` followed by `WMA(sqrt(n))`, not a recursive baseline with data-dependent gain. One conventional long
cell only; no window/filter/management grid.

## Frozen causal rule and costs

On completed 1h closes compute conventional HMA(20): weighted moving averages use linearly increasing weights, combine
`2*WMA(10)-WMA(20)`, then apply WMA(4). Enter at the next open only when close crosses above the HMA, HMA is rising, and
close is above EMA20. Exit when close is at/below HMA, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR
stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill
plus checksum-verified actual signed funding. Invalid state cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 129 raises the
numbered family to 129; any later promotion must use family-wise alpha <=`0.05/129` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional finite-weighted low-lag trend family.  
DEFERRED: short cell, alternate windows/filters, folds, holdout, Stage B and activation.
