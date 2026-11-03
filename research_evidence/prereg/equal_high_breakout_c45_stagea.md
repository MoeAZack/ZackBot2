# Candidate 45 / equal-high liquidity-pool breakout preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `equal_high_breakout_long_1h`; 1h long only.

## Queue, ledger and duplicate identity

This implements the existing scout queue's deferred equal-high/liquidity-pool structure idea after the simpler queued
families. Before freezing, the integrated Candidate 1–44 ledger/risk register, current tree, every local/remote ref and
tag, Git history, preregistration, result and research tool were searched for equal/repeated/clustered highs, liquidity
pools and double-top breakouts. No tested strategy, packet or result matched. The closest families were inspected:
Donchian/C30 break any prior channel high without a repeated-level condition; liquidity-sweep/FVG rejects a false break;
C43 requires overlapping balance plus two accepted closes. C45 instead requires separated near-equal resistance touches
and enters the first buffered close through that frozen level. It is not a rescue or variant of C24/C41 or C41–C44.

## Frozen causal rule and costs

At completed decision bar `t`, inspect only highs from `t-24..t-1`. Freeze resistance as their maximum. A touch is a
high within 0.25 current ATR of resistance; require at least two touches whose lag positions are separated by >=3 bars.
The decision close must be the first close >=0.10 ATR above resistance (the prior close was not above its then-known
24-bar edge), close >EMA50, and quote volume >=1.25 times its prior 48-bar median. A 2 ATR stop must be >=5x modeled
round-trip cost. Fill next open; exit below EMA20, at 2 ATR stop, 2R target or 16 bars. Costs are 5 bp taker +2 bp
adverse slippage per fill plus checksum-verified actual signed funding. One frozen cell; no parameter grid.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used alone to rewrite economics. Candidate 45 increases the numbered
tested-candidate family to 45; any later promotion must use family-wise alpha <=`0.05/45` and chronological OOS.
Therefore PAPER eligibility remains BLOCK regardless of this development result. No folds, holdout, rescue, PAPER or
runtime action is authorized here. The post-2026-09-28 holdout stays sealed.

ROADMAP FIT: genuinely unused price-structure hypothesis from the existing scout queue.  
DEFERRED: short mirror, tolerance/spacing variants, folds, holdout, Stage B and all activation.
