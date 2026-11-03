# Candidate 78 / Omega payoff-persistence preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `omega_payoff_persistence_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–77 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Omega ratio, gain-to-pain and rolling
positive-return-sum/negative-return-sum rules. No prior candidate uses this statistic. C42 counts signs, C51 measures
kurtosis, C57 compares semivariances and C73 contrasts trimmed versus raw location. They disclose distributional/trend
correlation but none measures realized gain mass per unit realized loss mass. One long cell; no grid.

## Frozen causal rule and costs

At every completed 1h decision bar, use exactly 48 completed log returns ending at `t`. The zero-threshold Omega ratio
is the sum of positive returns divided by the absolute sum of negative returns; a zero loss sum is ineligible. Enter long
at the next open only when Omega is >=1.50, the decision-hour return is positive and close is above EMA20. Exit when
Omega is <=1.0 or close reaches/breaches EMA20, after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR
stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill
plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 78 increases
the numbered family to 78; any later promotion must use family-wise alpha <=`0.05/78` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF payoff-distribution family.  
DEFERRED: short cells, other windows/thresholds, folds, holdout, Stage B and activation.
