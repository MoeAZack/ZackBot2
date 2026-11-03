# Candidate 71 / temporal volume concentration preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `volume_concentration_continuation_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–70 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for volume/turnover/participation
concentration, Herfindahl/HHI and volume shares. No prior candidate measures temporal concentration of turnover. C50
compares one bar with its same-hour seasonal baseline, VPI measures signed volume pressure, C35 accumulates OBV and C52
estimates lagged volume-return correlation. Their generic volume correlation is disclosed, but C71 tests the shape of
the full recent participation distribution without selecting an hour or volume direction. C70 supplies no parameter or
rule. One long cell; no grid.

## Frozen causal rule and costs

At every completed 1h decision bar, divide each of exactly 24 completed positive quote-volume observations ending at
`t` by their sum. The normalized HHI is `24 * sum(share^2)`, equal to one for uniform participation. Enter long at the
next open only when HHI is at least 2.0, the decision-hour return is positive and the completed 24-hour return is
positive. Exit when HHI is at most 1.25 or the 24-hour return is non-positive, after 24 completed bars, or earlier at
the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp
taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing/non-positive volume cannot
qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 71 increases the numbered family to 71;
any later promotion must use family-wise alpha <=`0.05/71` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF participation-concentration family.  
DEFERRED: short/reversal cells, alternative windows/thresholds/concentration metrics, folds, holdout, Stage B and activation.
