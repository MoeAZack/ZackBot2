# Candidate 52 / own-symbol volume-return lead-lag preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `volume_return_leadlag_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–51 append-only ledger/risk register, current tree, local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for volume-return correlation, lagged volume,
volume-leading-return and same-symbol volume prediction. No tested strategy, packet or result matched. BTC orderflow
lead-lag is cross-asset diffusion from independently archived BTC trades; VPI and OBV are contemporaneous signed-volume
accumulations; C50 is a same-hour level surprise with a contemporaneous price impulse. C52 instead estimates whether a
symbol's own lagged quote-volume changes historically predicted its next completed return, then applies that relationship
to the current volume change without using current return direction. Volume/momentum correlation is disclosed, but the
predictive statistic is materially different. One long cell, no grid.

## Frozen causal rule and costs

At every completed 1h bar `t`, set `x[t]=log(qv[t]/qv[t-1])` and `r[t]=close[t]/close[t-1]-1`. Over exactly the last 72
completed observations, calculate Pearson correlation between `x[s-1]` and `r[s]`. Enter long at the next open only if
that causal predictive correlation is >=0.25 and current quote volume is >=1.50 times the prior bar. A 2 ATR stop must
be >=5x modeled round-trip cost. Exit when the predictive correlation is <=0, at the 2 ATR stop, 2R target, or 8 bars.
Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Zero/missing quote
volume cannot qualify. No current-return, trend, symbol, hour or regime selection is permitted.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 52 increases the numbered family to 52;
any later promotion must use family-wise alpha <=`0.05/52` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF causal own-symbol information lead-lag family.  
DEFERRED: short/negative-correlation cells, other windows/thresholds, folds, holdout, Stage B and activation.
