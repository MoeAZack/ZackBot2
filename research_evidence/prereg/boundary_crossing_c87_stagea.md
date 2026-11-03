# Candidate 87 / EMA boundary-crossing intensity preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `boundary_crossing_persistence_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–86 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for crossing intensity/rate, EMA regime-
flip rate, zero crossing and boundary-crossing rules. No prior candidate uses this statistic. C44 estimates a conditional
bull-to-bull probability on a three-state six-hour normalized-return process, while C75 counts local turning points.
C87 instead measures the unconditional transition rate across a moving price boundary. Those state/path families are
related disclosures but materially distinct. One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, classify close as above or not above causal EMA20. Over the last 48 valid completed
state transitions, calculate the fraction whose state differs from the previous bar. Enter long at the next open only
when this boundary-crossing rate is <=0.10, the decision-hour and completed 24h returns are positive, and close is above
EMA20. Exit when crossing rate is >=0.25 or close is no longer above EMA20, after 24 completed bars, or earlier at the
2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker
+2 bp adverse slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 87 increases
the numbered family to 87; any later promotion must use family-wise alpha <=`0.05/87` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF boundary-crossing-intensity family.  
DEFERRED: short cells, other boundaries/windows/thresholds, folds, holdout, Stage B and activation.
