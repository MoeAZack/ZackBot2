# Candidate 103 Negative Volume Index crossover Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/negative-volume-index-c103`.
- Frozen head: `7cb5b3767bc783427ec5531b82be2fbd590b59d9`; tree `ef30ceb2f273dd170338ec9ad542921d893a7b62`.
- Preregistration SHA-256: `458afb1cb3f5c4d3730ca9e7f236623662aed61f3ba50abf4392de56fdd0deab`.
- Artifact: `C:\Dev\ZackBot_cowork_data\negative_volume_index_c103\negative-volume-index-long-dev.json`.
- Artifact SHA-256: `a0fd07422a84a2d0af3e96361ff5d3671ca9d365ab81bcd18f01ea31f8873d87`.
- Canonical packet SHA-256 (independently reproduced): `6fa6f6820aab0135c707e21861695b707782c5966a52c4c80a262c50389f1701`.

The frozen CORE-20 development run closed 466 long trades over all 18 symbols: 314 above and 152 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was +$128.657516. Fees were $83.773694 and actual signed funding was $4.467314,
leaving net P&L +$40.416507, net +10.452606R (mean +0.022430R), PF 1.046707, and max drawdown 20.845408R.

Passed gates: pre-cost gross positive, mean net R positive, sample >=100, symbols >=8, and >=20 closes in each regime.
Failed gates: PF >1.05 and max drawdown <=15R. PF misses by 0.003293 and drawdown exceeds the ceiling by 5.845408R;
neither is rounded or waived. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK. No NVI span, volume
definition, filter, or exit alteration can rescue this frozen cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
