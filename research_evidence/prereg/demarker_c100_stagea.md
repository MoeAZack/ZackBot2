# Candidate 100 / DeMarker oversold-recovery preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `demarker_recovery_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–99 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for DeMarker, DeMax, DeMin and incremental-
extrema oscillators. No prior candidate implements this statistic. Williams %R recovers from a close's range position,
MFI uses volume-weighted typical-price flow, Aroon measures time since extrema, and Vortex compares cross-bar travel.
C100 instead compares the magnitude of new high extensions with new low extensions. Generic oversold/trend correlation
is disclosed. One long cell using conventional settings; no horizon, threshold, or management grid.

## Frozen causal rule and costs

On each completed 1h bar, DeMax is `max(high-current minus high-prior, 0)` and DeMin is `max(low-prior minus low-current,
0)`. DeMarker(14) is the trailing sum of DeMax divided by the trailing sum of DeMax plus DeMin. Enter long at the next
open only when DeMarker crosses above the conventional 0.30 oversold boundary and close is above EMA20. Exit when it
reaches the conventional 0.70 boundary or falls back below 0.30, after 24 bars, or earlier at the 2 ATR stop/2R target.
A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage
per fill plus checksum-verified actual signed funding. Missing or zero-denominator history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 100 raises
the numbered family to 100; any later promotion must use family-wise alpha <=`0.05/100` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF incremental-extrema pressure family.  
DEFERRED: short cell, alternate horizons/levels/filters, folds, holdout, Stage B and activation.
