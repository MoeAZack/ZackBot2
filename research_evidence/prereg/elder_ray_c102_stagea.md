# Candidate 102 / Elder Ray improving-bear-power preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `elder_ray_pullback_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–101 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Elder Ray, bull power, bear power and
high/low displacement around an EMA. No prior candidate implements this mechanism. Legacy EMA pullbacks test a price
touch/cross, C55 uses wick imbalance, Vortex uses cross-bar travel, and C100 compares new extrema. C102 instead separates
the high and low distances from a rising EMA to require positive buying power while selling power improves but remains
negative. General trend/pullback correlation is disclosed. It is independent of C101's state-space result. One long
cell; no span, persistence, or management grid.

## Frozen causal rule and costs

On completed 1h bars compute EMA13, bull power `high-EMA13`, and bear power `low-EMA13`. Enter long at the next open when
EMA13 is rising, close is above EMA13, bull power is positive, bear power remains negative, and bear power increased on
each of the latest two transitions. Exit when bear power reaches zero or EMA13 stops rising, after 12 bars, or earlier
at the 1.5 ATR stop/1.5R target. A 1.5 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are
5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 102 raises
the numbered family to 102; any later promotion must use family-wise alpha <=`0.05/102` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF extrema-power pullback family.  
DEFERRED: short cell, alternate spans/persistence/filters, folds, holdout, Stage B and activation.
