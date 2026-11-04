# Candidate 127 / Elder Force Index zero-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `force_index_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–126 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Force Index, Elder Force and smoothed
price-change-times-volume. No prior candidate implements this statistic. Volume correlation with C35 OBV, C36 MFI, C52
volume/return lead-lag and C124 Ease of Movement is disclosed; C127 exponentially smooths signed close displacement
multiplied by contemporaneous quote liquidity, rather than accumulating signed volume, ranking money flow, estimating a
lag coefficient, or dividing range displacement by liquidity. One conventional long cell only; no span/filter/management
grid.

## Frozen causal rule and costs

On completed 1h bars compute raw Force Index as `(close-close[-1])*quote_volume`, then its conventional 13-bar EMA.
Enter at the next open only when the smoothed Force Index crosses above zero and close is above EMA20. Exit when Force
Index is at/below zero, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14%
taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual
signed funding. Invalid price or liquidity cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 127 raises the
numbered family to 127; any later promotion must use family-wise alpha <=`0.05/127` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional price/liquidity-force family.  
DEFERRED: short cell, alternate spans/filters, folds, holdout, Stage B and activation.
