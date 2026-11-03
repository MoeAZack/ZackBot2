# Candidate 97 causal Ichimoku cloud-cross Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/ichimoku-cloud-c97`.
- Frozen head: `b6fd6899ce24417ef80233db9ed779e3582d788f`; tree `4f240c950721ece846814b0bb7feee3366e13c87`.
- Preregistration SHA-256: `0ca3803df333819752972dfa1961a304cf24b66b8f8039c9d9abdfd4b04c5836`.
- Artifact: `C:\Dev\ZackBot_cowork_data\ichimoku_cloud_c97\ichimoku-cloud-long-dev.json`.
- Artifact SHA-256: `06a55dad898a4dfcaa61dd4c20e1c1416c8efe02eaee69e84974a09b40bcea87`.
- Canonical packet SHA-256 (independently reproduced): `1c06c532cad09667a23d409fea97c56e827b795f2e40bf866539dfc5b6a5b286`.

The frozen CORE-20 development run closed 438 long trades over all 18 symbols: 289 above and 149 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was +$92.464933. Fees were $100.318554 and actual signed funding was $11.473922,
leaving net P&L -$19.327544, net +1.060606R (mean +0.002421R), PF 0.984144, and max drawdown 46.015831R. Dollar P&L
and risk-normalized R differ because the shared runner sizes and normalizes each trade by its own frozen stop risk.

Passed gates: pre-cost gross positive, mean net R positive, sample >=100, symbols >=8, and >=20 closes in each regime.
Failed gates: PF >1.05 and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK. The
small gross edge is consumed by costs, losing dollars overall, while payoff quality and drawdown fail materially; no
horizon, cloud, threshold, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
