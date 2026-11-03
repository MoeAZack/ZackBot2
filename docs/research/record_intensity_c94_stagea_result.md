# Candidate 94 closing-record intensity Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/record-intensity-c94`.
- Frozen head: `243b881b29c17440626fe68af481f61fb140598d`; tree `8f2b5e61adc439e9fc05bac9dfc9233d28892b00`.
- Preregistration SHA-256: `272587c3b9030b4043753a0069562cbe1ba5725c372fa4870027401ae979ffde`.
- Artifact: `C:\Dev\ZackBot_cowork_data\record_intensity_c94\record-intensity-long-dev.json`.
- Artifact SHA-256: `4045ebe0a65731f5c8bb8a1bc4006876de49ef3a3afe3a0f42c9501b00e69028`.
- Canonical packet SHA-256 (independently reproduced): `d92cbc55037b944fde287f5246a520601cd50156666e2bd19bdd988e41b100fe`.

The frozen CORE-20 development run closed 971 long trades over all 18 symbols: 621 above and 350 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was +$30.238465. Fees were $204.440790 and actual signed funding was $20.763384,
leaving net P&L -$194.965709, net -34.259143R (mean -0.035282R), PF 0.921654, and max drawdown 100.978934R.

Passed gates: pre-cost gross positive, sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The small gross edge did not survive the frozen cost model and risk failed materially; no record horizon, intensity
threshold, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed post-2026-09-28 holdout was not accessed.
