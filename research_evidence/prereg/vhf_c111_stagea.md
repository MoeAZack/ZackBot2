# Candidate 111 / Vertical Horizontal Filter trend-onset preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `vhf_trend_onset_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–110 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Vertical Horizontal Filter, VHF, directional
range/path efficiency, and equivalent efficiency-ratio signals. No prior candidate implements this statistic. Trend and
range correlation is disclosed, but C111 measures the ratio of a close window's total directional extent to its cumulative
close-to-close travel rather than slope, breakout, volatility, or a moving-average speed. One long cell only; no grid.

## Frozen causal rule and costs

On completed 1h bars, VHF(28) is `(max(close)-min(close))/sum(abs(diff(close)))` over trailing observations. Enter at the
next open only when VHF crosses above 0.40 and close is above EMA20. Exit when VHF is below 0.20 or close is at/below EMA20,
after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip
cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Flat or missing
warm-up windows cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 111 raises the
numbered family to 111; any later promotion must use family-wise alpha <=`0.05/111` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF directional path-efficiency family.  
DEFERRED: short cell, alternate windows/thresholds/filters, folds, holdout, Stage B and activation.
