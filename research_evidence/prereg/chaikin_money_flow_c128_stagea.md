# Candidate 128 / Chaikin Money Flow zero-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `chaikin_money_flow_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–127 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Chaikin Money Flow, CMF oscillator, money-flow
multiplier and accumulation/distribution volume. No prior candidate implements this statistic. Volume correlation with C35
OBV, C36 MFI, C55 wick pressure, C124 Ease of Movement and C127 Force Index is disclosed; C128 weights quote liquidity by
the completed close's normalized location within its own high-low range and sums that balance over a fixed window, rather
than using close direction, typical-price direction, wick fractions, midpoint displacement, or close change. One
conventional long cell only; no window/filter/management grid.

## Frozen causal rule and costs

On completed 1h bars compute the money-flow multiplier `(2*close-high-low)/(high-low)`, multiply by quote volume, and
divide the rolling 20-bar sum by rolling 20-bar quote volume: conventional CMF(20). Enter at the next open only when CMF
crosses above zero and close is above EMA20. Exit when CMF is at/below zero, after 24 bars, or earlier at the 2 ATR
stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp
adverse slippage per fill plus checksum-verified actual signed funding. Zero/invalid range or liquidity cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 128 raises the
numbered family to 128; any later promotion must use family-wise alpha <=`0.05/128` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional range-location liquidity-flow family.  
DEFERRED: short cell, alternate windows/filters, folds, holdout, Stage B and activation.
