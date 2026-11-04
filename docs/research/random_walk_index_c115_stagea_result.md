# Candidate 115 Random Walk Index Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/random-walk-index-c115`.
- Frozen head: `7c74070b5a5db7c2b17c279916793839d2ed0f2f`; tree `0ca00b4121f30345aa34b9f16632b37bea993dbf`.
- Preregistration SHA-256: `a3905ca622f6f8635b7b8a69819ff802edf947e5350c0457d65ad6f4f8d31c07`.
- Artifact: `C:\Dev\ZackBot_cowork_data\random_walk_index_c115\random-walk-long-dev.json`.
- Artifact SHA-256: `bc85863ea12e7a5c8ce34f7c38ef1b60183edf80c48caaec8fac63e401ab3459`.
- Canonical packet SHA-256 (independently reproduced): `2180f35ae52b895103c611aeff05b0129775df5172d8d572d414752dcaf4bc9d`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 2,580 long trades over all 18 symbols: 1,601 above and 979 below the BTC
4h EMA200 regime boundary. Pre-cost gross was -$68.804332. Fees were $315.847929 and actual signed funding was
$9.966514, leaving net P&L -$394.618775, net -140.015714R (mean -0.054270R), PF 0.791249, and max drawdown
144.290422R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The ATR-scaled directional random-walk break has no edge before costs and fails payoff/risk materially; no horizon,
threshold, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
