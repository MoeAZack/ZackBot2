# Candidate 40 / return-autocorrelation persistence long — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT**. The frozen candidate is research-only and default-OFF. No folds,
holdout, PAPER/testnet or runtime action ran.

## Identity and frozen head

- Strategy: `return_autocorr_persistence_long_1h`, 1h long only.
- Frozen head: `b20fd8aa219ba6a15dd2523dcdf32167fc75cd3a`.
- Distinct hypothesis: a fresh upside impulse continues only during measured positive lag-1 return dependence; this is
  a statistical serial-correlation state, not an oscillator recovery, moving-average crossover or cumulative momentum rule.
- Before freezing, the current tree plus all branches, remotes, tags and Git history were searched for return
  autocorrelation, serial correlation, return persistence, lag-1 dependence and equivalent rolling-correlation signal
  code. No earlier tested strategy, preregistration, result or packet matched; historical hits were unrelated lead/lag text.

## Immutable artifact

- Path: `C:\Dev\ZackBot_cowork_data\retac1h_c40\return-autocorr-long-dev.json`
- Canonical packet SHA-256: `810a699688506baaef2bbf32d70b79a6b4c5b7b4f29229d6da0e903875d13c46`
- File SHA-256: `953bf46c29bf0a10e287b4650df47080664d197f802027599e93b8957a22b05b`
- Development window: CORE-20, `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`.
- Costs: 5 bp taker fee plus 2 bp adverse slippage per fill and checksum-verified actual funding.

## Result and hard gates

- 389 closes across 18 symbols; BTC regimes: 262 above / 127 below EMA200.
- Gross: -$104.077584; fees: $65.214153; funding: $4.836346; net P&L: -$174.128083.
- Net: -39.305617R; mean: -0.101043R/trade; PF: 0.776835; max DD: 70.630796R; win rate: 37.5321%.
- PASS: sample >=100, breadth >=8 symbols, each BTC regime >=20.
- FAIL: pre-cost gross positive, mean net R positive, PF >1.05, max DD <=15R.

The preregistered development gate failed, so evaluation stopped. The sealed holdout remained unopened; no folds were
authorized by this contract. No parameter or threshold changed after seeing the result, and no rescue variant was run.

ROADMAP FIT: continuous strategy conveyor development rejection record.  
DEFERRED: folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and all rescue/tuning variants.
