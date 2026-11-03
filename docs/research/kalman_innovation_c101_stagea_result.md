# Candidate 101 local-level Kalman innovation-recovery Stage-A result

Verdict: **STAGE-A PASS / PAPER BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/kalman-innovation-c101`.
- Frozen head: `8c00e74b0cc6ffd16d0746ba8fdedc008a18aefd`; tree `592a82ca0f88631e0ad6efec8f7927b28f67848f`.
- Preregistration SHA-256: `cffda63c5825711d8f37abee0e49f38c1d6856a30d41dbb2c966aa856de5a473`.
- Artifact: `C:\Dev\ZackBot_cowork_data\kalman_innovation_c101\kalman-innovation-long-dev.json`.
- Artifact SHA-256: `867996d0e7556b1441d947bff75d4936a557f762956e23f24964a739da231f0c`.
- Canonical packet SHA-256 (independently reproduced): `101d939f6b52adeda1720b434c997b0feff762eb395e24d51bdd2817f31e611e`.

The frozen CORE-20 development run closed 144 long trades over all 18 symbols: 80 above and 64 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was +$34.503941. Fees were $18.443010 and actual signed funding was -$0.069819,
leaving net P&L +$16.130750, net +3.778759R (mean +0.026241R), PF 1.074975, and max drawdown 9.689494R.

All frozen Stage-A gates passed: pre-cost gross positive, mean net R positive, PF >1.05, sample >=100, symbols >=8,
>=20 closes in each regime, and max drawdown <=15R. This is only a development survivor. PF exceeds its threshold by
just 0.024975 and the numbered family is now 101, so selection/multiplicity risk remains substantial. Isolated PAPER
eligibility remains BLOCK exactly as preregistered. No tuning, folds, holdout access, or activation is authorized here.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
