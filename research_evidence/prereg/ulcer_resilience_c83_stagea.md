# Candidate 83 / Ulcer-resilience preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `ulcer_resilience_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–82 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Ulcer Index, RMS/squared drawdown,
drawdown-severity and shallow-drawdown resilience rules. No prior candidate uses this statistic. C58 measures drawdown
duration and recovery, while C59 ranks cross-sectional return volatility. Those risk-state families are related
disclosures but do not measure the root-mean-square depth below a rolling high. One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, compute the 48h rolling high, each completed close's percentage drawdown from that
causal rolling high, and the square root of the 48-bar mean squared drawdown. Enter long at the next open only when this
Ulcer Index is <=3%, the decision-hour and completed 24h returns are positive, and close is above EMA20. Exit when the
Ulcer Index is >=5% or the 24h return is non-positive, after 24 completed bars, or earlier at the 2 ATR stop/2R target.
A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage
per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 83 increases
the numbered family to 83; any later promotion must use family-wise alpha <=`0.05/83` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF drawdown-severity family.  
DEFERRED: short cells, other windows/thresholds, folds, holdout, Stage B and activation.
