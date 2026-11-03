# Candidate 57 / downside-semivariance rebound preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `downside_semivariance_rebound_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–56 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for semivariance, downside/upside realized
variance, variance asymmetry and downside-risk rebound. No tested strategy, packet or result decomposes realized
variance by return sign or trades their ratio. Realized skew uses a signed third central moment; C51 uses the direction-
neutral fourth moment; ATR shock and volatility acceleration compare ranges across horizons. C57 instead tests whether
second-moment downside energy dominating upside energy represents forced-selling risk that rebounds on the first positive
hour. Distribution/mean-reversion correlation is disclosed, but the semivariance mechanism is materially distinct. C56
remains frozen BLOCK and supplies no parameter or rule. One long cell; no ratio/window/management grid.

## Frozen causal rule and costs

At every completed 1h decision bar `t`, compute exactly 168 close-to-close returns ending at `t`. Downside semivariance
is the mean squared negative part; upside semivariance is the mean squared positive part. Enter long at the next open
only when downside/upside semivariance is >=2.0 and the current completed return is positive. A 2 ATR stop must be >=5x
modeled 0.14% taker-plus-slippage round-trip cost. Exit when the ratio is <=1.0, after 24 completed bars, or earlier at
the 2 ATR stop/2R target. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual signed
funding. Missing history or zero upside semivariance cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 57 increases the numbered family to 57;
any later promotion must use family-wise alpha <=`0.05/57` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF downside-risk decomposition family.  
DEFERRED: short cell, alternative windows/ratios, folds, holdout, Stage B and activation.
