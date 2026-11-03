# Candidate 95 return-rank shift Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/rank-shift-c95`.
- Frozen head: `defea66a14766fb2164f42b541441c98b7d911c2`; tree `f28ab47c3881a554a832a89218e9c57e04a1e190`.
- Preregistration SHA-256: `376cc349922bd74fd8fae3451156195caf8b2b4bc5b943675e3da5079456d810`.
- Artifact: `C:\Dev\ZackBot_cowork_data\rank_shift_c95\rank-shift-long-dev.json`.
- Artifact SHA-256: `2a42da422be73cd3430d5a46676861667d81729c7c06a0fe3adbed61c4d1feb0`.
- Canonical packet SHA-256 (independently reproduced): `5374882bab11f313761cdb83671c83417bde91024e2f16e1a7ba956dda448cf6`.

The frozen CORE-20 development run closed 427 long trades over all 18 symbols: 266 above and 161 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$79.743095. Fees were $69.866846 and actual signed funding was $5.668067,
leaving net P&L -$155.278008, net -31.219572R (mean -0.073114R), PF 0.853284, and max drawdown 58.924377R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen nonparametric recent-versus-baseline return-distribution shift has no development edge before costs, and the
risk gate failed materially; no dominance threshold, window, or exit alteration can rescue this cell. No parameter
tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
