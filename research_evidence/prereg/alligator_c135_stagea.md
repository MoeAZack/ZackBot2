# Candidate 135 / Williams Alligator bullish-alignment preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `alligator_alignment_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–134 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Williams Alligator, Alligator indicator,
jaw/teeth/lips and standard 13/8/5 displaced smoothed-average terms. No prior candidate implements this statistic.
Trend correlation with EMA cells, C97 Ichimoku, C117 KAMA, C123 McGinley, C129 HMA and C132 AO is disclosed; C135 uniquely
uses three median-price Wilder-smoothed averages with conventional 8/5/3 display displacement, interpreted causally as
lagged values at the decision bar. One conventional long cell only; no period/displacement/filter/management grid.

## Frozen causal rule and costs

On completed 1h bars compute median price `(high+low)/2`. Jaw is SMMA13 lagged eight bars, teeth SMMA8 lagged five bars,
and lips SMMA5 lagged three bars. These lags reproduce values visible at the current bar without consuming the chart's
forward displacement as future information. Enter at the next open only on the first bar where lips > teeth > jaw and
close > lips. Exit when lips <= teeth, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x
modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-
verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 135 raises the
numbered family to 135; any later promotion must use family-wise alpha <=`0.05/135` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional displaced median-price smoothed-alignment family.  
DEFERRED: short cell, alternate periods/displacements/filters, folds, holdout, Stage B and activation.
