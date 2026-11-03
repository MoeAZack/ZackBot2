# Candidate 58 / drawdown-duration recovery preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `drawdown_duration_recovery_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–57 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for drawdown age/duration, underwater age,
bars since a high, recovery hazard and prolonged-drawdown recovery. Existing underwater references are performance-risk
metrics, not signals. RSI, Williams %R and Keltner recovery cells use instantaneous price/oscillator location; C57 uses
return semivariance; three-bar exhaustion uses a fixed candle sequence. C58 instead makes continuous time spent below a
rolling high a required state before a recovery trigger. Mean-reversion correlation is disclosed, but this path-duration
hazard is materially distinct. C56/C57 remain frozen BLOCK and supply no parameter or rule. One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, calculate the rolling maximum of exactly 168 closes and the consecutive count of
completed bars strictly below that rolling high. The prior bar must have been at least 48 consecutive bars underwater
and at least 10% below its rolling high. Enter long at the next open only when the decision close crosses from at/below
EMA20 to above EMA20. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Exit on a close below
EMA20, recovery to the rolling high, after 24 completed bars, or earlier at the 2 ATR stop/2R target. Costs are 5 bp taker
+2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 58 increases the numbered family to 58;
any later promotion must use family-wise alpha <=`0.05/58` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF path-duration recovery family.  
DEFERRED: short cell, alternative ages/drawdowns/high windows, folds, holdout, Stage B and activation.
