# Candidate 130 / Chande Momentum Oscillator zero-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `cmo_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–129 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Chande Momentum Oscillator, CMO oscillator and
fixed-window gain-minus-loss balance. No prior candidate implements this statistic. Momentum correlation with the RSI-based
C23/C33 cells and C126 TSI is disclosed; C130 uses unsmoothed rolling sums and the symmetric ratio
`100*(sum_gains-sum_losses)/(sum_gains+sum_losses)`, rather than Wilder averaging, divergence, or double exponential
smoothing. One conventional long cell only; no window/filter/management grid.

## Frozen causal rule and costs

On completed 1h closes compute conventional CMO(14) from rolling sums of positive and absolute negative one-bar changes.
Enter at the next open only when CMO crosses above zero and close is above EMA20. Exit when CMO is at/below zero, after
24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip
cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Zero/invalid
denominator cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 130 raises the
numbered family to 130; any later promotion must use family-wise alpha <=`0.05/130` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional fixed-window momentum-balance family.  
DEFERRED: short cell, alternate windows/filters, folds, holdout, Stage B and activation.
