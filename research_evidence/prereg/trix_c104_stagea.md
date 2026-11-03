# Candidate 104 / TRIX zero-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `trix_zero_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–103 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for TRIX, triple-exponential and triple-
smoothed rate-of-change signals. No prior candidate implements this statistic. C39 measures an ATR-normalized spread
between two EMAs, while legacy momentum cells combine an EMA cross with endpoint returns. C104 instead takes the
one-bar rate of change of a price series smoothed recursively three times. Trend correlation is disclosed. It is
independent of C101–C103. One long cell with the conventional span; no span/threshold/management grid.

## Frozen causal rule and costs

On completed 1h closes, recursively calculate three adjust-false EMA(15) series, each applied to the preceding EMA
output. TRIX is 100 times the one-bar percentage change of the third EMA. Enter long at the next open only when TRIX
crosses above zero and close is above EMA20. Exit when TRIX is non-positive, after 24 bars, or earlier at the 2 ATR
stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp
adverse slippage per fill plus checksum-verified actual signed funding. Missing warm-up history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 104 raises
the numbered family to 104; any later promotion must use family-wise alpha <=`0.05/104` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF triple-smoothed momentum family.  
DEFERRED: short cell, alternate spans/signals/filters, folds, holdout, Stage B and activation.
