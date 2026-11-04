# Candidate 136 Price Volume Trend Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/pvt-c136`.
- Frozen head: `2c222fa5eb7f8408b41913360015e450833d3bf5`; tree `2d3ea8a2c5b74207e8c88166df98ec3e2c81ba3d`.
- Preregistration SHA-256: `843205c668dae131aaf1040e8c8d38e7abf89576d1d86ff4e0e8bbbf12d51562`.
- Artifact: `C:\Dev\ZackBot_cowork_data\pvt_c136\pvt-long-dev.json`.
- Artifact SHA-256: `9687f93df89141c709a005798457739451ca437e60340ea824e444b12fe9b77b`.
- Canonical packet SHA-256 (independently reproduced): `19cb7bb8a16c387a886153b505b7ad513b8e7aabd565a0d2403bdd329cb05ea2`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,356 long trades over all 18 symbols: 822 above and 534 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$142.790135. Fees were $181.214556 and actual signed funding was $12.582287,
leaving net P&L -$336.586978, net -96.946490R (mean -0.071494R), PF 0.806437, and max drawdown 105.400051R. Exit counts
were 1,012 signal, 124 stop, 54 time, and 166 target.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean net
R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK. The
frozen cumulative relative-return-volume crossover lacks even a pre-cost edge and risk/payoff is materially inadequate;
no span, filter, threshold, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
