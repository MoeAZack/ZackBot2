# Candidate 84 / Directional-change overshoot preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `directional_change_overshoot_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–83 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for directional-change, event-time
overshoot, threshold-reversal and equivalent ZigZag-event rules. No prior strategy candidate uses this statistic; the
few history hits for "overshoot" were unrelated execution-risk prose. C75 counts ordinal turning points, while fixed
window breakout candidates compare price with rolling levels. C84 instead maintains an event-time state and extrema,
then emits only the threshold-confirming reversal event. One long cell; no grid.

## Frozen causal rule and costs

Process completed 1h closes sequentially. Maintain a trough in down/initial state and a peak in up/initial state. Emit
an upward directional-change event when log price rises from the maintained trough by at least `1.0 * ATR / close`,
switching to up state; emit the symmetric downward event from the maintained peak and switch to down state. Enter long
at the next open only on the upward event when completed 24h return is positive and close is above EMA20. Exit on the
downward event or non-positive 24h return, after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop
must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus
checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 84 increases
the numbered family to 84; any later promotion must use family-wise alpha <=`0.05/84` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF event-time directional-change family.  
DEFERRED: short cells, other thresholds/state rules, folds, holdout, Stage B and activation.
