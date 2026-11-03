# Candidate 76 / Aroon extreme-recency persistence preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `aroon_recency_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–75 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Aroon, extreme recency and age of
rolling highs/lows. No prior candidate uses this statistic. Channel breakouts test a breach beyond a prior boundary;
C40 autocorrelation, C56 regression quality and C75 turning-point rate describe path persistence. They disclose trend
correlation but do not compare the event times of the latest rolling high and low. One long cell; no grid.

## Frozen causal rule and costs

At every completed 1h decision bar, use exactly 25 completed highs and lows ending at `t`. Aroon-up and Aroon-down are
the latest high/low indices scaled from zero at the oldest observation to 100 at the newest. Enter long at the next open
only when Aroon-up minus Aroon-down is >=70, the decision-hour return is positive and close is above EMA20. Exit when
the oscillator is <=0 or close reaches/breaches EMA20, after 24 completed bars, or earlier at the 2 ATR stop/2R target.
A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage
per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 76 increases
the numbered family to 76; any later promotion must use family-wise alpha <=`0.05/76` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF extreme-event-recency family.  
DEFERRED: short cells, other windows/thresholds, folds, holdout, Stage B and activation.
