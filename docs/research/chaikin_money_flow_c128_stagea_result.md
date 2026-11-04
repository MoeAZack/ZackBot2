# Candidate 128 Chaikin Money Flow Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/chaikin-money-flow-c128`.
- Frozen head: `6ca9355837b92fc458036974b18ff6be5838d4de`; tree `61767daa6ed57375ccd446ee9808e8041409b399`.
- Preregistration SHA-256: `b85543e441fbb723bb22385f6966f3e06a927f1caa63a4a2ed52ca7a82d03143`.
- Artifact: `C:\Dev\ZackBot_cowork_data\chaikin_money_flow_c128\cmf-long-dev.json`.
- Artifact SHA-256: `c2aa1c96e53e7dc62101f5f8cb73a092dcdcc56ee99cbccc9741eb3f658abf41`.
- Canonical packet SHA-256 (independently reproduced): `99780cc315d247eb837b0717f98e9848f935851405825152b7f320b59c26cf9b`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,404 long trades over all 18 symbols: 884 above and 520 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$144.432952. Fees were $179.208305 and actual signed funding was $10.649148,
leaving net P&L -$334.290404, net -98.201902R (mean -0.069944R), PF 0.781089, and max drawdown 105.641341R. Exit
counts were 1,068 signal, 165 stop, 41 time, and 130 target.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen range-location liquidity-flow zero-cross has no edge before costs and fails payoff/risk materially; no window,
filter, threshold, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
