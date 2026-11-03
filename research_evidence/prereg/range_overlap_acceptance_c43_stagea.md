# Candidate 43 / range-overlap price-acceptance breakout preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `range_overlap_acceptance_long_1h`; 1h long only.

Before freezing, the current tree, all local/remote refs and tags, Git history, preregistrations, result documents and
research ledgers were searched for range-overlap density, overlap ratios, price acceptance and acceptance breakouts.
The only text hit was Fibonacci zone intersection, which is unrelated. The nearest trials were inspected: C30 enters
the first 24h breakout with trend/volume expansion; VER-15 requires Bollinger compression and a touch/reclaim retest;
ORH-15 uses a fixed UTC opening range; MBP-1H uses a 4h breakout plus later 1h pullback. None measures rolling adjacent
candle-range overlap or requires two consecutive closes beyond its causally frozen rolling edge.

At completed decision bar `t`, define each adjacent pair's overlap as intersection / union of its high-low intervals.
The balance score is the mean of 11 pair ratios across the 12 bars ending `t-2`; require >=0.55. Freeze the range edge
as the highest high of the 24 bars ending `t-2`. Bar `t-1` must be the first close above that edge (the `t-2` close was
not above it), and `t` must close >=0.10 ATR beyond the same edge. Also require close >EMA50, quote volume >= its prior
48-bar median, and a 2 ATR stop >=5x modeled round-trip cost. Fill next open. Exit below EMA20, at 2 ATR stop, 2R target
or 16 bars. Costs are 5 bp taker +2 bp adverse slippage per fill plus actual checksum-verified signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if pre-cost gross<=0, mean net R<=0,
PF<=1.05, <100 closes, <8 symbols, DD>15R, or either BTC regime <20. Inspect all gates. This task ends after this single
development evaluation regardless of result: no rescue, folds, holdout, PAPER or runtime action.

Multiplicity: one frozen cell. Default-OFF/research-only. Survivorship-biased current-universe evidence.

ROADMAP FIT: orthogonal structural-balance continuation scout after C42.  
DEFERRED: short mirror, alternate overlap definitions/thresholds, folds, holdout, Stage B and every activation action.
