# Candidate 96 / Parabolic SAR bullish-flip preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `parabolic_sar_flip_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–95 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Parabolic SAR, Wilder SAR, parabolic
stops, accelerating extreme points and acceleration factors. No prior candidate implements this statistic or state
machine. EMA-spread, regression-trend, Aroon-recency and Vortex candidates disclose broad trend correlation, but use
fixed-window summaries; C96 uniquely advances a path-dependent stop toward each new extreme and flips only when price
crosses it. Variance-ratio and close-location alternatives were rejected as too correlated with C63 and C55. One long
cell; no acceleration, filter, or management grid.

## Frozen causal rule and costs

Compute Wilder Parabolic SAR sequentially on completed 1h high/low bars, seeded from the first two completed closes.
The acceleration factor starts at 0.02 after each state change, rises by 0.02 for each new trend extreme, and is capped
at 0.20. Bull-state SAR is constrained below the preceding two lows; bear-state SAR is constrained above the preceding
two highs. Enter long at the next open only on a completed-bar bear-to-bull flip with close above EMA20. Exit on the next
completed-bar bull-to-bear flip, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled
0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified
actual signed funding. Missing/non-finite history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 96 increases
the numbered family to 96; any later promotion must use family-wise alpha <=`0.05/96` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF path-dependent accelerating-extreme trend family.  
DEFERRED: short cell, alternate acceleration/filter/management rules, folds, holdout, Stage B and activation.
