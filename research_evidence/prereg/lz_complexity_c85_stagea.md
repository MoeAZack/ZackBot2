# Candidate 85 / LZ sign-sequence complexity preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `lz_sign_complexity_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–84 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for Lempel-Ziv/LZ78, sign-sequence
complexity, return compressibility and algorithmic-complexity rules. No prior candidate uses this statistic. C42 uses
the marginal Shannon entropy of sign counts and discards ordering; C44 estimates a one-step transition probability.
C85 parses the full ordered sign string into new phrases. Those information/state families are disclosed but materially
distinct. One long cell; no grid.

## Frozen causal rule and costs

At each completed 1h decision bar, encode the last 32 completed log returns as one for positive and zero otherwise.
Parse left-to-right with an initially empty LZ78 dictionary: at each position add the shortest phrase not already in the
dictionary and advance by its length; count a final already-seen suffix once. Normalize as
`phrase_count * log2(32) / 32`. Enter long at the next open only when complexity is <=1.40, the decision-hour and
completed 24h returns are positive, and close is above EMA20. Exit when complexity is >=1.65 or the 24h return is
non-positive, after 24 completed bars, or earlier at the 2 ATR stop/2R target. A 2 ATR stop must be >=5x modeled 0.14%
taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual
signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 85 increases
the numbered family to 85; any later promotion must use family-wise alpha <=`0.05/85` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF ordered-sequence-complexity family.  
DEFERRED: short cells, other encodings/windows/thresholds, folds, holdout, Stage B and activation.
