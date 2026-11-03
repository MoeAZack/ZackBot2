# Candidate 54 / BTC-beta residual reversion preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `btc_beta_residual_reversion_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–53 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for beta, BTC-relative return, residual,
idiosyncratic shock, pair/statistical arbitrage and cross-sectional reversal. No tested strategy, packet or result uses
a rolling BTC beta to isolate and fade a one-hour idiosyncratic return residual. Candidate 24 rotates into persistent
30-day risk-adjusted relative strength; rank-breadth reversal fades seven-day cross-sectional extremes only during broad
crowding; candidate 21 follows BTC aggressor-flow diffusion into locally flat alts. C54 instead estimates each symbol's
own BTC return exposure using prior bars, removes the contemporaneous BTC component and fades only an extreme negative
residual. Cross-asset/rank correlation is disclosed, but the dynamic beta-neutral shock mechanism is materially distinct.
One long cell; no beta, z-score, holding-period or side grid.

## Frozen causal rule and costs

At every completed 1h decision bar `t`, calculate target and BTC close-to-close returns. Estimate target beta to BTC from
exactly 336 observations ending at `t-1`. The residual at `t` is target return minus that frozen beta times BTC return.
Standardize it using exactly 168 residual observations ending at `t-1`. Enter long at the next open only when the current
residual z-score is <=-2.5. A 1.5 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Exit when the
residual z-score is non-negative, after four completed bars, or earlier at the 1.5 ATR stop/1.5R target. Costs are 5 bp
taker +2 bp adverse slippage per fill plus checksum-verified actual signed funding. Missing history cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 54 increases the numbered family to 54;
any later promotion must use family-wise alpha <=`0.05/54` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF cross-asset statistical-arbitrage family.  
DEFERRED: short cell, pair selection, beta/z/holding variants, folds, holdout, Stage B and activation.
