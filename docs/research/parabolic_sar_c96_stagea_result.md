# Candidate 96 Parabolic SAR bullish-flip Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/parabolic-sar-c96`.
- Frozen head: `079e1843152d487738cddf25790529d9cc632846`; tree `1f7e2e6a8b613820dd415219688ea5f5270c2e3c`.
- Preregistration SHA-256: `d9badcb8b5011d69649c17b948d7e54d61828129e74cf4d493b0d7b876509a6c`.
- Artifact: `C:\Dev\ZackBot_cowork_data\parabolic_sar_c96\parabolic-sar-long-dev.json`.
- Artifact SHA-256: `7c12299c66f8e88c2773ceb7dbb41316e95e5202315b277b3aca17dd77790310`.
- Canonical packet SHA-256 (independently reproduced): `7d1295eb036ec2c788935a31977a71643c9b1147bddfaadc070914c2a851f5ce`.

The frozen CORE-20 development run closed 965 long trades over all 18 symbols: 599 above and 366 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$53.300107. Fees were $166.786584 and actual signed funding was $17.147143,
leaving net P&L -$237.233834, net -50.277740R (mean -0.052101R), PF 0.882558, and max drawdown 90.527248R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen path-dependent accelerating-extreme flip has no development edge before costs and its drawdown is far beyond
the risk ceiling; no acceleration factor, filter, or exit alteration can rescue this cell. No parameter tuning is
authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
