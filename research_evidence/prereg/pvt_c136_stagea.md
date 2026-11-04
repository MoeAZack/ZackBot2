# Candidate 136 / Price Volume Trend crossover preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `pvt_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–135 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Price Volume Trend, PVT indicator and cumulative
volume-times-percent-change terms. No prior candidate implements this statistic. Volume-family correlation with C35 OBV,
C36 MFI, C124 EOM, C127 Force Index, C128 CMF and C131 Klinger is disclosed; PVT uniquely accumulates each bar's base volume
times its relative close-to-close return, rather than direction-only volume, absolute price change, intrabar location, or
trend-range normalization. One conventional long cell only; no span/filter/management grid.

## Frozen causal rule and costs

On completed 1h bars compute conventional PVT as the cumulative sum of base volume times the current close-to-close
percentage change. Its signal is EMA20 of PVT. Enter at the next open only when PVT crosses above its signal and close is
above EMA20. Exit when PVT is at/below its signal, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must
be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus
checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 136 raises the
numbered family to 136; any later promotion must use family-wise alpha <=`0.05/136` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional cumulative relative-return-volume family.  
DEFERRED: short cell, alternate signal spans/filters, folds, holdout, Stage B and activation.
