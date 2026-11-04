# Candidate 133 Connors RSI Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/connors-rsi-c133`.
- Frozen head: `2a80b966619b1c0ec446e0cbe1005354ba993452`; tree `e3462d3c7f346399a9ecfa4b0cbec2548e5827b8`.
- Preregistration SHA-256: `edc7d60396f1140075a5e278bca141787ff2e68b9303bc2c259137172519309c`.
- Artifact: `C:\Dev\ZackBot_cowork_data\connors_rsi_c133\connors-rsi-long-dev.json`.
- Artifact SHA-256: `21f74a2dd564a5923536a8cb322b26d74d7aeaf8226e64bd81c07a0cbe7f2cf3`.
- Canonical packet SHA-256 (independently reproduced): `befad04d1dc54b9e9e8b26e4d7c83e40ff4beae2ddbc3f133e8b6e34f9ec21e1`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 277 long trades over all 18 symbols: 220 above and 57 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was $17.175596. Fees were $47.915799 and actual signed funding was $1.733533,
leaving net P&L -$32.473737, net -5.910864R (mean -0.021339R), PF 0.892361, and max drawdown 14.699093R. Exit counts
were 225 signal, 50 stop, and 2 target.

Passed gates: pre-cost gross positive, sample >=100, symbols >=8, >=20 closes in each regime, and max drawdown <=15R.
Failed gates: mean net R positive and PF >1.05. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK. The
small positive gross is erased by frozen realistic costs and payoff quality is inadequate; no component, threshold,
filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
