# Candidate 53 / learned hour-of-day return drift preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `hour_of_day_drift_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–52 append-only ledger/risk register, current tree, local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for same-hour returns, hour-of-day return means,
intraday return seasonality and learned hourly drift. No tested strategy, packet or result matched. Session inventory
unwind trades two fixed handoffs after a price displacement/reversal; weekend premium trades two fixed weekly transitions;
C50 normalizes volume by hour but also requires a current price impulse. C53 instead estimates each symbol's next-hour
return from prior daily realizations of precisely that future UTC-hour bucket, without current price, volume, trend,
symbol or regime selection. Calendar/session correlation is disclosed, but this learned causal seasonality is materially
different. One long cell, no hour ranking or grid.

## Frozen causal rule and costs

At every completed 1h decision bar `t`, identify the UTC hour entered at `t+1`. Compute the arithmetic mean of exactly
28 completed close-to-close returns ending at `t+1-24d`, for `d=1..28`. Enter long at the next open only when that mean
is >=+0.20%, a gross hurdle above the modeled 0.14% taker-plus-slippage round trip. A 1.5 ATR stop must also be >=5x
modeled round-trip cost. Exit after one completed bar or earlier at the 1.5 ATR stop/1.5R target. Costs are 5 bp taker
+2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 53 increases the numbered family to 53;
any later promotion must use family-wise alpha <=`0.05/53` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF learned calendar-seasonality family.  
DEFERRED: short cell, hour selection/ranking, lookback/hurdle variants, folds, holdout, Stage B and activation.
