# Candidate 91 / Corwin-Schultz implied-spread preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `corwin_schultz_spread_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–90 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Corwin-Schultz, high-low spread
estimators, implied bid-ask spread and two-bar latent spread rules. No prior candidate uses this estimator. C31 measures
volume-scaled close-return price impact and C61 compares Parkinson high-low variance with close variance; C91 instead
uses the two-bar high/low geometry to estimate a latent proportional bid-ask spread. Those microstructure/range families
are disclosed but materially distinct. One long cell; no grid.

## Frozen causal rule and costs

For each completed adjacent pair of 1h bars, let beta be the sum of the two squared log high/low ranges and gamma the
squared log ratio of their joint maximum high to joint minimum low. With `k=3-2*sqrt(2)`, calculate
`alpha=(sqrt(2*beta)-sqrt(beta))/k-sqrt(gamma/k)`, floor alpha at zero, and transform it to proportional spread
`2*(exp(alpha)-1)/(1+exp(alpha))`. Use the median of the last 24 completed estimates. Enter long at the next open only
when the median implied spread is <=0.003, the current completed-hour and completed 24h returns are positive, and close
is above EMA20. Exit when spread is >=0.006 or the 24h return is non-positive, after 24 completed bars, or earlier at
the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs remain the
conservative fixed model—5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding—and are
not replaced or reduced by the estimator.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 91 increases
the numbered family to 91; any later promotion must use family-wise alpha <=`0.05/91` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF OHLC-implied-liquidity family.  
DEFERRED: short cells, other windows/spread thresholds/estimators, folds, holdout, Stage B and activation.
