# Candidate 133 / Connors RSI oversold-recovery preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `connors_rsi_recovery_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–132 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Connors RSI, ConnorsRSI, streak RSI and return
PercentRank terms. No prior candidate implements this statistic. Balance of Power was discarded before evaluation because
C98 already covers its normalized candle-body mechanism; Donchian was discarded because it is a legacy tested strategy.
Correlation with the RSI recovery/divergence cells C23/C33, Williams %R C38 and Laguerre RSI C116 is disclosed; C133 uniquely
averages price RSI(3), signed consecutive-streak RSI(2), and the 100-bar percentile rank of one-bar returns. One conventional
long cell only; no component/threshold/management grid.

## Frozen causal rule and costs

On completed 1h closes compute conventional Connors RSI as the equal average of RSI(3) on close, RSI(2) on the signed
consecutive up/down streak length, and the 100-bar PercentRank of the current one-bar return. Enter at the next open only
when CRSI recovers above 10 after being at/below 10 and close is above EMA200. Exit when CRSI reaches 70, after 24 bars, or
earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are
5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 133 raises the
numbered family to 133; any later promotion must use family-wise alpha <=`0.05/133` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional composite short-term mean-reversion family.  
DEFERRED: short cell, alternate components/thresholds, folds, holdout, Stage B and activation.
