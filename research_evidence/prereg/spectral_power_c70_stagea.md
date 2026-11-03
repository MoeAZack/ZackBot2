# Candidate 70 / low-frequency return-power preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `low_frequency_power_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–69 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Fourier/FFT, periodograms, power
spectra, frequency-domain signals, spectral concentration and dominant cycles. No prior candidate uses frequency-domain
return power. C40 estimates one time-domain autocorrelation lag, C56 fits a time-domain price slope, and C63 compares
variance at two fixed horizons. Their generic persistence correlation is disclosed, but C70 measures the distribution
of demeaned return variance across orthogonal Fourier frequencies. C69 supplies no parameter or rule. One long cell;
no grid.

## Frozen causal rule and costs

At every completed 1h decision bar use exactly 168 completed close-to-close log returns ending at `t`. Demean them,
compute the real FFT, square coefficient magnitudes, exclude the DC coefficient, and divide power in non-DC bins 1
through 7 by total non-DC power. Enter long at the next open only when this low-frequency share is at least 0.15, the
decision-hour return is positive and the completed 24-hour return is positive. Exit when the share is at most 0.10 or
the 24-hour return is non-positive, after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must
be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus
checksum-verified actual signed funding. Missing or zero-power history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 70 increases the numbered family to 70;
any later promotion must use family-wise alpha <=`0.05/70` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF frequency-domain persistence family.  
DEFERRED: short/cycle-timing cells, alternative windows/bin sets/thresholds, folds, holdout, Stage B and activation.
