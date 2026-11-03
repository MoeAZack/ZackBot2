# Candidate 50 / intraday-seasonality-normalized volume surprise preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `hourly_volume_surprise_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–49 append-only ledger/risk register, current tree, local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for same-hour volume baselines, hour-of-day volume
normalization, intraday volume seasonality and hourly volume surprise. No tested strategy, packet or result matched.
Adjacent mechanisms were inspected: momentum burst and C30 use generic rolling volume; VPI uses cumulative signed
volume after absorption; C35 uses cumulative OBV; large-trade impulse uses independent aggTrades. C50 instead removes
hour-of-day participation seasonality by comparing the decision bar only with the same UTC hour on prior days, then
tests continuation of a contemporaneous upside impulse. Momentum/volume beta is the disclosed nearest correlation, but
the normalization information is new. One long cell, no grid or rescue variant.

## Frozen causal rule and costs

At completed 1h bar `t`, compute the median quote volume of exactly `t-24h`, `t-48h`, ..., `t-336h`. Require current
quote volume >=2.0 times that 14-observation same-hour median, close minus the prior close >=0.75 current ATR, and close
>EMA50. A 2 ATR stop must be >=5x modeled round-trip cost. Fill the next open; exit below EMA20, at the 2 ATR stop, 2R
target, or 12 bars. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding.
All inputs are completed-bar causal; missing same-hour history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 50 increases the numbered family to 50;
any later promotion must use family-wise alpha <=`0.05/50` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially different causal intraday-seasonality information family, default-OFF.  
DEFERRED: short cell, alternate baselines/thresholds, folds, holdout, Stage B and all activation.
