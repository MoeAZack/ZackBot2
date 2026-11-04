# Candidate 125 / Commodity Channel Index recovery preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `cci_recovery_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–124 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Commodity Channel Index, CCI oscillator and
typical-price mean deviation. No prior candidate implements this statistic. Broad oversold-oscillator correlation with C38
Williams %R and C100 DeMarker, and standardized-location correlation with C92, are disclosed; C125 uniquely standardizes
typical price around its rolling arithmetic mean by rolling mean absolute deviation rather than range rank, extrema changes,
or median/MAD shock. One conventional long cell only; no window/threshold/filter/management grid.

## Frozen causal rule and costs

On completed 1h bars compute conventional CCI(20): typical price `(high+low+close)/3`, its 20-bar mean, and
`(typical-mean)/(0.015*mean_absolute_deviation)`. Enter at the next open only when CCI crosses above -100 from below,
the close is below EMA20, and the decision close is higher than the prior close. Exit when CCI reaches +100, after 24
bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost.
Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Zero/invalid deviation
cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 125 raises the
numbered family to 125; any later promotion must use family-wise alpha <=`0.05/125` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional typical-price-deviation recovery family.  
DEFERRED: short cell, alternate windows/thresholds/filters, folds, holdout, Stage B and activation.
