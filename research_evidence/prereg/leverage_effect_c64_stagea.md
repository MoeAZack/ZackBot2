# Candidate 64 / leverage-effect downside continuation preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `leverage_effect_downside_short_1h`; 1h short only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–63 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for leverage effects, volatility feedback,
return/variance covariance, return-volatility cross-moments and realized-variance shocks. No prior candidate uses the
rolling correlation between returns and changes in squared returns. C41 compares volatility levels across horizons,
C57 compares downside/upside semivariance, and C60 estimates autocorrelation of squared returns. These volatility-family
and generic short-momentum correlations are disclosed, but C64 measures a contemporaneous first/second-moment coupling.
C63 supplies no parameter or rule. One short cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, calculate close return, squared return and the change in squared return. Over exactly
168 completed observations, calculate Pearson correlation between return and squared-return change. Enter short at the
next open only when correlation is at most -0.25, the 24h close return is negative, and the decision-hour return is
negative. Exit when correlation is non-negative, the 24h return is non-negative, after 24 completed bars, or earlier at
the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp
taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 64 increases the numbered family to 64;
any later promotion must use family-wise alpha <=`0.05/64` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF return/variance cross-moment family.  
DEFERRED: long/rebound cell, alternative windows/correlations, folds, holdout, Stage B and activation.
