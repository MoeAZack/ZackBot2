# Candidate 132 / Awesome Oscillator zero-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `awesome_oscillator_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–131 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Awesome Oscillator, AO zero-cross, median-price
SMA5-minus-SMA34 and Accelerator Oscillator terms. No prior candidate implements this statistic. The initially considered
Elder Ray family was discarded before evaluation as a duplicate of C102. Trend/momentum correlation with C39 EMA-spread,
C104 TRIX, C126 TSI, C129 HMA and C130 CMO is disclosed; C132 uniquely uses the conventional unweighted 5/34 moving-average
difference of bar median price. One conventional long cell only; no window/filter/management grid.

## Frozen causal rule and costs

On completed 1h bars compute conventional Awesome Oscillator as SMA5 of `(high+low)/2` minus SMA34 of the same causal
median price. Enter at the next open only when AO crosses above zero and close is above EMA20. Exit when AO is at/below
zero, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage
round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 132 raises the
numbered family to 132; any later promotion must use family-wise alpha <=`0.05/132` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional median-price dual-SMA momentum family.  
DEFERRED: short cell, alternate windows/filters, folds, holdout, Stage B and activation.
