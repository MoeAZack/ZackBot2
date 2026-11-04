# Candidate 126 / True Strength Index crossover preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `tsi_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–125 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for True Strength Index, TSI oscillator and
double-smoothed signed-to-absolute momentum. No prior candidate implements this statistic. Momentum correlation with C104
TRIX and C121 SMI is disclosed, but C126 divides double-smoothed one-bar signed momentum by identically smoothed absolute
momentum; TRIX differentiates triple-smoothed price and SMI normalizes smoothed range-midpoint displacement by smoothed
half-range. One conventional long cell only; no span/filter/management grid.

## Frozen causal rule and costs

On completed 1h closes compute conventional TSI(25,13): double-exponentially smooth one-bar close changes and their
absolute values with spans 25 then 13, then take `100*signed/absolute`. Smooth TSI with a 7-bar EMA signal line. Enter
at the next open only when TSI crosses above its signal while TSI is positive and close is above EMA20. Exit when TSI is
at/below its signal, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14%
taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual
signed funding. Zero/invalid denominator cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 126 raises the
numbered family to 126; any later promotion must use family-wise alpha <=`0.05/126` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional normalized-smoothed-momentum family.  
DEFERRED: short cell, alternate spans/filters, folds, holdout, Stage B and activation.
