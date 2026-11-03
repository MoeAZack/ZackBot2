# Candidate 102 Elder Ray improving-bear-power Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/elder-ray-c102`.
- Frozen head: `55d99b778abc40d6cb0d8b031541c8605711deca`; tree `539f3eb9032ba9d5e246f2a1915512d735a283d5`.
- Preregistration SHA-256: `24726704b667a5309c1bc4904138fa54f258094368afda09cf72f59dc0d5490c`.
- Artifact: `C:\Dev\ZackBot_cowork_data\elder_ray_c102\elder-ray-long-dev.json`.
- Artifact SHA-256: `25fa2ba766e5de3ea80c24115dbf5df57bbb808b3bd41246171e7321cd6f6cc0`.
- Canonical packet SHA-256 (independently reproduced): `f4cf2e00d71bc680fb183321f0dff684d2279c9b26e54bf9a262fa8da4732319`.

The frozen CORE-20 development run closed 2,382 long trades over all 18 symbols: 1,389 above and 993 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$255.646616. Fees were $203.442967 and actual signed funding was $3.127188,
leaving net P&L -$462.216771, net -244.825009R (mean -0.102781R), PF 0.567564, and max drawdown 245.558791R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen extrema-power pullback has no edge before costs and payoff/risk fail severely; no EMA span, persistence,
filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
