# Candidate 60 / volatility-clustering downside continuation preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `volatility_clustering_downside_short_1h`; 1h short only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–59 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for volatility clustering, squared-return
serial dependence, conditional heteroskedasticity, ARCH/GARCH and variance persistence. No prior candidate uses lag-1
correlation of squared returns as its information variable. C40 estimates signed-return autocorrelation, C41 compares
short and long volatility levels, C51 uses the fourth moment, and C57 compares downside/upside semivariance. Those
volatility-family correlations and generic downside-momentum exposure are disclosed, but C60 tests conditional-variance
persistence rather than direction persistence, volatility acceleration or tail shape. C59 failure supplies no parameter
or rule. One short cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, calculate close-to-close returns and their squares. Over exactly 168 completed pairs,
calculate Pearson correlation between squared return at `t` and squared return at `t-1`. Enter short at the next open
only when this correlation is at least 0.20, the 24h close return is negative, and the current completed-hour return is
negative. Exit when correlation is non-positive, the 24h return is non-negative, after 24 completed bars, or earlier at
the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp
taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 60 increases the numbered family to 60;
any later promotion must use family-wise alpha <=`0.05/60` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF conditional-variance-persistence family.  
DEFERRED: long cell, alternative windows/correlation thresholds, folds, holdout, Stage B and activation.
