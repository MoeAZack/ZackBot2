# Candidate 39 / EMA spread expansion long — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT**. The frozen candidate is research-only and default-OFF. No folds,
holdout, PAPER/testnet or runtime action ran.

## Identity and frozen head

- Strategy: `ema_spread_expansion_long_1h`, 1h long only.
- Frozen head: `2200b6b6faddc6b9a6cb7f03c2f9df6a1e9ee3d2`.
- Distinct hypothesis: ATR-normalized EMA20/EMA50 spread expands after recent moving-average convergence;
  this is not a single-oscillator recovery or a price-volatility squeeze.
- Before freezing, the current tree plus all branches, remotes, tags and Git history were searched for EMA/moving-average
  compression, EMA-spread expansion/breakout, moving-average spread, and equivalent normalized-spread signal code.
  No earlier strategy, preregistration, result or packet matched. The post-freeze repository has 676 refs; the only
  matching history is the frozen Candidate-39 commit itself.

## Immutable artifact

- Path: `C:\Dev\ZackBot_cowork_data\emaspread1h_c39\ema-spread-long-dev.json`
- Canonical packet SHA-256: `65ed7de4ff8a928ed2672cd07a3d10a6f8bb7ca10fd731e10c99f583b1247a7c`
- File SHA-256: `64b908fb5cb99bb811902a6d789ee4b790f2ae3d969a5188a8e2f2559a9c9a83`
- Development window: CORE-20, `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`.
- Costs: 5 bp taker fee plus 2 bp adverse slippage per fill and checksum-verified actual funding.

## Result and hard gates

- 235 closes across 18 symbols; BTC regimes: 143 above / 92 below EMA200.
- Gross: $15.610765; fees: $48.424391; funding: $5.332528; net P&L: -$38.146154.
- Net: -4.667577R; mean: -0.019862R/trade; PF: 0.949869; max DD: 38.466371R; win rate: 40.8511%.
- PASS: pre-cost gross positive, sample >=100, breadth >=8 symbols, each BTC regime >=20.
- FAIL: mean net R positive, PF >1.05, max DD <=15R.

The preregistered development gate failed, so evaluation stopped before folds and the sealed holdout remained unopened.
No parameter or threshold changed after seeing the result, and no rescue variant was run.

ROADMAP FIT: continuous strategy conveyor development rejection record.  
DEFERRED: folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and all rescue/tuning variants.
