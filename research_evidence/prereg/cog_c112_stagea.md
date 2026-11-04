# Candidate 112 / Center-of-Gravity zero-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `cog_zero_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–111 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Center/Centre of Gravity, COG, Ehlers balance
points and gravity oscillators. No prior candidate implements this statistic. Generic price and weighted-window correlation
is disclosed, but C112 estimates where recent median-price mass balances in time rather than measuring endpoint return,
moving-average spread, price location, or path efficiency. One conventional long cell only; no grid.

## Frozen causal rule and costs

On completed 1h bars use median price `(high+low)/2` over 10 bars. Number the current observation 1 and the oldest 10;
COG is `-sum(weight*price)/sum(price)+(10+1)/2`. Enter at the next open only when COG crosses above zero and close is
above EMA20. Exit when COG is non-positive, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be
>=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus
checksum-verified actual signed funding. Missing warm-up cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 112 raises the
numbered family to 112; any later promotion must use family-wise alpha <=`0.05/112` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF temporal price-balance family.  
DEFERRED: short cell, alternate windows/thresholds/filters, folds, holdout, Stage B and activation.
