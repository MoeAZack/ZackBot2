# Candidate 131 / Klinger Volume Oscillator crossover preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 4 November 2026 Cairo before results.  
Key: `klinger_volume_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–130 append-only ledger/risk register, current tree, all local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for Klinger Volume Oscillator, KVO oscillator and
volume-force/cumulative-measurement terms. No prior candidate implements this statistic. Correlation with C35 OBV, C36 MFI,
C124 EOM, C127 Force Index and C128 CMF is disclosed; C131 uniquely signs volume by the direction of high+low+close and
normalizes price range by a trend-dependent cumulative measurement before fixed 34/55 exponential differencing. One
conventional long cell only; no span/filter/management grid.

## Frozen causal rule and costs

On completed 1h closes compute conventional Klinger volume force: trend is the sign of the change in high+low+close;
measurement is high-low; cumulative measurement adds the current range while trend persists and resets to the prior plus
current range on a trend change; volume force is quote volume times `abs(2*measurement/cumulative_measurement-1)`, trend and
100. KVO is EMA34(volume force)-EMA55(volume force), with EMA13 signal. Enter at the next open only when KVO crosses above
its signal and close is above EMA20. Exit when KVO is at/below signal, after 24 bars, or earlier at the
2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +
2 bp adverse slippage per fill plus checksum-verified actual signed funding. Invalid cumulative measurement cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net R>0,
PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 131 raises the
numbered family to 131; any later promotion must use family-wise alpha <=`0.05/131` and chronological OOS. PAPER eligibility
remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the
post-2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF conventional signed price-trend volume-force family.  
DEFERRED: short cell, alternate spans/filters, folds, holdout, Stage B and activation.
