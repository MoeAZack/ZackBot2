# Candidate 134 / Know Sure Thing crossover preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `kst_cross_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–133 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Know Sure Thing, KST oscillator and the standard
ROC10/15/20/30 weighted composite. No prior candidate implements this statistic. Correlation with C39 EMA spread, C104 TRIX,
C107 Coppock, C126 TSI, C130 CMO and C132 Awesome Oscillator is disclosed; C134 uniquely combines four separately smoothed
rate-of-change horizons with conventional 1/2/3/4 weights and a nine-bar signal. One conventional long cell only; no
horizon/weight/filter/management grid.

## Frozen causal rule and costs

On completed 1h closes compute the conventional KST: SMA10 of ROC10 plus two times SMA10 of ROC15, three times SMA10 of
ROC20, and four times SMA15 of ROC30. Its signal is SMA9 of KST. Enter at the next open only when KST crosses above its
signal and close is above EMA20. Exit when KST is at/below its signal, after 24 bars, or earlier at the
2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +
2 bp adverse slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 134 raises the
numbered family to 134; any later promotion must use family-wise alpha <=`0.05/134` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional weighted multi-horizon smoothed-ROC family.  
DEFERRED: short cell, alternate horizons/weights/filters, folds, holdout, Stage B and activation.
