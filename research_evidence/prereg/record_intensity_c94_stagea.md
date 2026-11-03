# Candidate 94 / closing-record intensity preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `record_high_intensity_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–93 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for record rates, record intensity,
record-setting frequency and rolling new-high counts. No prior candidate uses this statistic. C45 tests a single
equal-high liquidity-pool breakout, C68 a round-number crossing, C69 low price recurrence, and C76 the recency of the
last high/low. C94 instead measures the frequency of strict independently evaluated trailing closing records over a
second window. Those breakout/extrema families are disclosed but materially distinct. One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h bar, mark one when its close strictly exceeds the maximum of the prior 24 completed closes and zero
otherwise. At each decision bar, record intensity is the mean of those marks over exactly 48 completed bars. Enter long
at the next open only when intensity is >=0.10, the current completed-hour and completed 24h returns are positive, and
close is above EMA20. Exit when intensity is <=0.02 or the 24h return is non-positive, after 24 completed bars, or
earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs
are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing trailing history
cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 94 increases
the numbered family to 94; any later promotion must use family-wise alpha <=`0.05/94` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF record-statistics persistence family.  
DEFERRED: low-record reversal/short cells, other horizons/windows/thresholds, folds, holdout, Stage B and activation.
