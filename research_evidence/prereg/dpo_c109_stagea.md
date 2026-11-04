# Candidate 109 / detrended-price zero-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `dpo_zero_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–108 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for DPO, detrended-price oscillators, centered or
displaced moving-average residuals. No prior candidate implements this statistic. Qstick was considered and rejected before
freeze as materially duplicative of C98 signed-body pressure. C109 compares current price with a trailing mean observed 11
bars earlier rather than a current trend average; generic moving-average correlation is disclosed. One long cell; no grid.

## Frozen causal rule and costs

On completed 1h bars calculate SMA20 using trailing closes and displace that fully known series by 11 bars. DPO is current
close minus that 11-bar-old SMA20 value. Enter at the next open only when DPO crosses above zero and close is above EMA20.
Exit when DPO is non-positive, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled
0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual
signed funding. No centered/future observations are used; missing warm-up cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 109 raises the
numbered family to 109; any later promotion must use family-wise alpha <=`0.05/109` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF lag-displaced price-residual family.  
DEFERRED: short cell, alternate lookbacks/displacements/filters, folds, holdout, Stage B and activation.
