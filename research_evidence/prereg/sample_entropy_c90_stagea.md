# Candidate 90 / magnitude-aware Sample Entropy preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `sample_entropy_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–89 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Sample Entropy, Approximate Entropy,
template-matching entropy, subsequence recurrence and magnitude entropy. No prior candidate uses this statistic. C42
computes entropy after discarding return magnitudes into signs, C85 applies Lempel-Ziv parsing to a binary sign string,
and C69 counts price-level revisits. C90 instead tests recurrence of real-valued return-magnitude subsequences. Those
complexity/recurrence families are disclosed but materially distinct. One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, use exactly 48 completed log returns. With template length `m=2` and Chebyshev
matching radius `0.20` times the population standard deviation of those returns, count distinct matching template pairs
of lengths two and three. Sample Entropy is minus the natural logarithm of the length-three match count divided by the
length-two match count. Zero match counts or zero variance cannot qualify. Enter long at the next open only when Sample
Entropy is <=0.80, the current completed-hour log return and completed 24h return are positive, and close is above EMA20.
Exit when entropy is >=1.20 or the 24h return is non-positive, after 24 completed bars, or earlier at the 2 ATR stop/2R
target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse
slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 90 increases
the numbered family to 90; any later promotion must use family-wise alpha <=`0.05/90` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF real-valued subsequence-complexity family.  
DEFERRED: short cells, other windows/templates/radii/thresholds, folds, holdout, Stage B and activation.
