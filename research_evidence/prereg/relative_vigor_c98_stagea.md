# Candidate 98 / Relative Vigor bullish-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `relative_vigor_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–97 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Relative Vigor Index, Balance of Power,
normalized candle body, body-to-range pressure and open-close vigor. No prior candidate implements this statistic. C55
measures lower-minus-upper wick rejection without using body direction; C30 combines close location with volume and a
channel; sign-entropy ignores magnitude and range. C98 instead tests smoothed close-minus-open auction pressure divided
by smoothed high-low range. Broad long/trend correlation is disclosed. One long cell; no horizon or threshold grid.

## Frozen causal rule and costs

On each completed 1h candle, form numerator `close-open` and denominator `high-low`. Smooth each causally with classic
four-bar weights `[1,2,2,1]/6`, divide, then take the trailing 10-bar mean as RVI. Its signal is a second causal four-bar
weighted mean of RVI. Enter long at the next open only when RVI crosses above its signal, RVI is positive, and close is
above EMA20. Exit when RVI falls below its signal or reaches zero, after 24 bars, or earlier at the 2 ATR stop/2R target.
A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage
per fill plus checksum-verified actual signed funding. Zero-range or missing-history windows cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 98 increases
the numbered family to 98; any later promotion must use family-wise alpha <=`0.05/98` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF normalized intrabar-body pressure family.  
DEFERRED: short cell, alternate horizons/smoothing/filters, folds, holdout, Stage B and activation.
