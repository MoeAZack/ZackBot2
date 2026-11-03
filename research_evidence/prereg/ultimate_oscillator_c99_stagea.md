# Candidate 99 / Ultimate Oscillator bullish-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `ultimate_oscillator_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–98 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Ultimate Oscillator, gap-aware buying
pressure, true-range pressure and weighted 7/14/28 oscillators. No prior candidate implements this statistic. Williams
%R uses one rolling close position, MFI adds volume to typical-price flow, and C98 normalizes close-open candle bodies.
C99 instead measures gap-aware close-to-true-low buying pressure over three fixed horizons relative to true range.
Generic oscillator/trend correlation is disclosed. One long cell; no horizon, level, or management grid.

## Frozen causal rule and costs

For each completed 1h bar define true low as `min(low, prior close)`, true high as `max(high, prior close)`, buying
pressure as `close-true low`, and true range as `true high-true low`. For horizons 7, 14 and 28, divide the rolling sum
of buying pressure by rolling true range. Ultimate Oscillator is 100 times their fixed 4:2:1 weighted mean. Enter long
at the next open only when the oscillator crosses above 50 and close is above EMA20. Exit below 50, after 24 bars, or
earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs
are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing or zero-range
history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 99 increases
the numbered family to 99; any later promotion must use family-wise alpha <=`0.05/99` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF gap-aware multi-horizon buying-pressure family.  
DEFERRED: short cell, alternate horizons/levels/filters, folds, holdout, Stage B and activation.
