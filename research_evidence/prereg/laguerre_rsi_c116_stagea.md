# Candidate 116 / Laguerre RSI preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `laguerre_rsi_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–115 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Laguerre filters, Laguerre RSI, four-stage
recursive directional energy, and equivalent signals. No prior candidate implements this statistic. Ordinary RSI and
recursive-filter correlation is disclosed, but C116 sums positive/negative differences between four Laguerre states rather
than rolling close gains/losses or a moving-average spread. One conventional gamma/threshold cell only; no grid.

## Frozen causal rule and costs

On completed 1h closes recursively calculate four Laguerre states using gamma 0.5: `L0=(1-g)*close+g*priorL0`,
`L1=-g*L0+priorL0+g*priorL1`, with the same recursion through L3. Sum upward and downward magnitudes across L0-L1,
L1-L2 and L2-L3; LRSI is `up/(up+down)`. Enter at the next open only when LRSI crosses above 0.5 and close is above
EMA20. Exit when LRSI is at/below 0.5, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x
modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus actual funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 116 raises the
numbered family to 116; any later promotion must use family-wise alpha <=`0.05/116` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF recursive Laguerre directional-energy family.  
DEFERRED: short cell, alternate gamma/thresholds/filters, folds, holdout, Stage B and activation.
