# Candidate 51 / realized-kurtosis tail-risk preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `realized_kurtosis_risk_short_1h`; 1h short only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–50 append-only ledger/risk register, current tree, local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for kurtosis, fourth-moment, leptokurtic and
fat-tail rules. No tested strategy, packet or result matched. The adjacent higher-moment C16 realized-skew cells measure
signed third-moment asymmetry; transient-jump measures one intrahour event; C41 measures variance acceleration; exhaustion
rules measure ordered candles. C51 instead measures direction-neutral tail concentration over seven days and tests the
short-side lottery-demand/risk-premium hypothesis once daily. Higher-moment and volatility correlation is disclosed, but
the information statistic and economic mechanism are distinct. One cell, no grid.

## Frozen causal rule and costs

At each completed 00:00 UTC 1h candle, compute pandas/Fisher sample excess kurtosis over the last 168 completed hourly
close returns, including the decision return. Enter short at the next open only when excess kurtosis >=5.0. A 2.5 ATR
stop must be >=5x modeled round-trip cost. Exit when kurtosis is <=0, at the 2.5 ATR stop, 2.5R target, or 24 bars.
Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. All inputs are
completed-bar causal; no trend, direction, symbol or regime selection is permitted.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 51 increases the numbered family to 51;
any later promotion must use family-wise alpha <=`0.05/51` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF fourth-moment tail-risk factor.  
DEFERRED: long cell, window/threshold variants, folds, holdout, Stage B and activation.
