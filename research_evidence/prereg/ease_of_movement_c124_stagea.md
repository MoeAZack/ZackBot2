# Candidate 124 / Ease of Movement zero-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `ease_of_movement_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–123 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Ease of Movement, midpoint displacement, box
ratio and range-per-volume efficiency. No prior candidate implements this statistic. Liquidity correlation with C31 Amihud
impact reversion and volume correlation with C35 OBV/C36 MFI are disclosed, but C124 measures signed high-low midpoint
travel multiplied by contemporaneous range per quote-volume unit, rather than shock return-per-dollar, cumulative volume
direction, or typical-price money flow. KST was rejected before freeze because its weighted ROC construction overlaps C107
Coppock. One conventional long cell only; no window/filter/management grid.

## Frozen causal rule and costs

On each completed 1h bar compute midpoint `(high+low)/2`, its one-bar change, and raw Ease of Movement as
`midpoint_change*(high-low)/quote_volume`; smooth it with a simple 14-bar mean. Enter at the next open only when the
smoothed value crosses above zero and close is above EMA20. Exit when the value is at/below zero, after 24 bars, or
earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs
are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Zero/invalid volume cannot
qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 124 raises the
numbered family to 124; any later promotion must use family-wise alpha <=`0.05/124` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional range/liquidity-efficiency family.  
DEFERRED: short cell, alternate windows/filters, folds, holdout, Stage B and activation.
