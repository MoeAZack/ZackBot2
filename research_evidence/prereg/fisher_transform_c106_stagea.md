# Candidate 106 / Fisher-transform zero-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `fisher_zero_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–105 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Fisher/Ehlers transforms, inverse Fisher, and
Gaussianized price-location signals. No prior candidate implements this statistic. C38 uses raw Williams %R recovery and
generic price-location correlation is disclosed; C106 uniquely applies two recursive smoothing stages around a nonlinear
log-odds transform of rolling median-price location. One conventional long cell only; no threshold/lookback/management grid.

## Frozen causal rule and costs

On completed 1h bars, locate median price `(high+low)/2` within its trailing 10-bar min/max and scale to [-1,+1]. Smooth
recursively as `value=clip(0.33*location+0.67*prior_value, ±0.999)` and calculate the conventional Fisher series as
`0.5*ln((1+value)/(1-value))+0.5*prior_fisher`. Enter at the next open only when Fisher crosses above zero and close is
above EMA20. Exit when Fisher is non-positive, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be
>=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus
checksum-verified actual signed funding. Missing/flat warm-up windows cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 106 raises the
numbered family to 106; any later promotion must use family-wise alpha <=`0.05/106` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF recursively Gaussianized price-location family.  
DEFERRED: short cell, alternate lookbacks/thresholds/filters, folds, holdout, Stage B and activation.
