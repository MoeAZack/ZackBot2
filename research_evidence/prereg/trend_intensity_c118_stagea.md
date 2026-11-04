# Candidate 118 / Trend Intensity Index preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `trend_intensity_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–117 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Trend Intensity Index, TII, and ratios of summed
positive/negative deviations from a moving average. No prior candidate implements this statistic. Moving-average and trend
correlation is disclosed, but C118 measures the distribution of deviation mass over 30 observations rather than a current
crossover, endpoint return, slope, or adaptive smoothing speed. One conventional long cell only; no parameter grid.

## Frozen causal rule and costs

On completed 1h closes calculate SMA60 and each close's signed deviation from it. Over 30 bars separately sum positive
and absolute negative deviations; TII is `100*positive/(positive+negative)`. Enter at the next open only when TII crosses
above 50 and close is above EMA20. Exit when TII is at/below 50, after 24 bars, or earlier at the 2 ATR stop/2R target. A
2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per
fill plus checksum-verified actual signed funding. Missing warm-up cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 118 raises the
numbered family to 118; any later promotion must use family-wise alpha <=`0.05/118` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF deviation-distribution trend family.  
DEFERRED: short cell, alternate windows/thresholds/filters, folds, holdout, Stage B and activation.
