# Candidate 104 TRIX zero-cross Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/trix-c104`.
- Frozen head: `9903110ffd0b689f07cd9b12b94883898ed87e0c`; tree `d2734bedea9245f98f0eb960abe83c1d272683b7`.
- Preregistration SHA-256: `83f783ab2f942268828f63d2e2c75f7090bfc031a8afbbd676410a2cf37e7428`.
- Artifact: `C:\Dev\ZackBot_cowork_data\trix_c104\trix-long-dev.json`.
- Artifact SHA-256: `cdb0fc41218c5e24c94d95ebf2178f6346e13ee44922f6421d29a3310c6b4115`.
- Canonical packet SHA-256 (independently reproduced): `2f2932004ae2418031d7ba3f54c9d745472747724ffc0361200189bb65023cd2`.

The frozen CORE-20 development run closed 430 long trades over all 18 symbols: 259 above and 171 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$72.105828. Fees were $75.195893 and actual signed funding was $7.448212,
leaving net P&L -$154.749933, net -30.844514R (mean -0.071731R), PF 0.864761, and max drawdown 55.326249R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen triple-smoothed momentum transition has no edge before costs and payoff/risk fail materially; no TRIX span,
signal, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
