# Candidate 103 / Negative Volume Index crossover preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `negative_volume_index_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–102 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Negative Volume Index, NVI, selective
low-volume accumulation and price returns conditioned on declining volume. No prior candidate implements this mechanism.
OBV signs every bar's volume, MFI uses volume-weighted typical-price flow, C52 tests lagged volume-return correlation,
and C71 measures temporal volume concentration. C103 instead compounds returns only when quote volume falls from the
prior hour. Volume/trend correlation is disclosed. It is independent of C101/C102. One long cell; no span or grid.

## Frozen causal rule and costs

Initialize NVI at 1000. On each completed 1h bar, compound its close-to-close return into NVI only when completed quote
volume is below the preceding completed hour; otherwise leave NVI unchanged. Enter long at the next open only when NVI
crosses above its 255-bar causal EMA and price is above EMA20. Exit when NVI falls below its EMA, after 24 bars, or
earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs
are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing volume/history
cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 103 raises
the numbered family to 103; any later promotion must use family-wise alpha <=`0.05/103` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF selective low-volume accumulation family.  
DEFERRED: short cell, alternate spans/volume definitions/filters, folds, holdout, Stage B and activation.
