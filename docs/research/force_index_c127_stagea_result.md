# Candidate 127 Elder Force Index Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/force-index-c127`.
- Frozen head: `c8f99adcdd89f25167270641353f0aa5330cd949`; tree `6ee8f96459b935306a7ca98024083c985c2d4a17`.
- Preregistration SHA-256: `78bd1f46c44e851b3ccce83fb2a05afc4bfa3ef31637ad1da7258dcda22f4021`.
- Artifact: `C:\Dev\ZackBot_cowork_data\force_index_c127\force-long-dev.json`.
- Artifact SHA-256: `1130910587f9feed567610e292afa47a42d76b31cd11b448c7a64f63da20cb91`.
- Canonical packet SHA-256 (independently reproduced): `a7eddac38bae4479d9cc1c0b4664e3493428d86840dacaf9231274668e9df44d`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,601 long trades over all 18 symbols: 947 above and 654 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$46.108063. Fees were $246.723360 and actual signed funding was $15.464944,
leaving net P&L -$308.296367, net -79.865605R (mean -0.049885R), PF 0.856668, and max drawdown 104.499880R. Exit
counts were 1,283 signal, 105 stop, 25 time, and 188 target.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen price/liquidity-force zero-cross has no edge before costs and fails payoff/risk materially; no span, filter,
liquidity scaling, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
