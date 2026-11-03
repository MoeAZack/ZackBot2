# Candidate 97 / causal Ichimoku cloud-cross preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `ichimoku_cloud_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–96 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Ichimoku, Tenkan, Kijun, Senkou,
cloud crosses and dual-midrange equilibrium. No prior candidate implements this statistic. Generic Donchian strategies
break a single trailing extreme; EMA/Aroon/Vortex and C96 disclose broad trend correlation but use moving averages,
extreme recency, directional travel, or an accelerating stop. C97 instead requires a short/medium range-midpoint cross
above a separately positive long-range midpoint cloud. One long cell; no horizon, displacement, or management grid.

## Frozen causal rule and costs

On each completed 1h bar, define conversion as the midpoint of the trailing 9-bar high/low range and base as the midpoint
of the trailing 26-bar range. Cloud A is their mean; cloud B is the midpoint of the trailing 52-bar range. No visual
forward displacement is used: all values end at the decision bar. Enter long at the next open only when conversion
crosses above base, cloud A is above cloud B, and close is above the higher cloud edge. Exit when conversion falls below
base or close falls below the lower cloud edge, after 24 bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must
be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus
checksum-verified actual signed funding. Missing history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 97 increases
the numbered family to 97; any later promotion must use family-wise alpha <=`0.05/97` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF dual-midrange equilibrium family.  
DEFERRED: short cell, alternate horizons/displacement/management, folds, holdout, Stage B and activation.
