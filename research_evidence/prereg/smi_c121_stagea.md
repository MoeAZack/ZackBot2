# Candidate 121 / Stochastic Momentum Index preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `smi_zero_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–120 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Stochastic Momentum Index, SMI, double-smoothed
range-midpoint displacement, and equivalent signals. No prior candidate implements this statistic. C106 Fisher transforms
raw rolling price location nonlinearly; C121 separately double-smooths midpoint displacement and half-range before their
ratio. Stochastic/momentum correlation is disclosed. Sign entropy was rejected before freeze as a direct C42 duplicate.

## Frozen causal rule and costs

On completed 1h bars calculate the 14-bar rolling high, low, midpoint and half-range. Double-smooth both `close-midpoint`
and half-range with EMA3 then EMA3. SMI is 100 times the smoothed displacement divided by smoothed half-range. Enter at the
next open only when SMI crosses above zero and close is above EMA20. Exit when SMI is at/below zero, after 24 bars, or
earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are
5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing/flat windows cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 121 raises the
numbered family to 121; any later promotion must use family-wise alpha <=`0.05/121` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF double-smoothed range-midpoint momentum family.  
DEFERRED: short cell, alternate windows/smoothing/thresholds/filters, folds, holdout, Stage B and activation.
