# Candidate 93 causal AR(2) return-forecast Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/ar2-forecast-c93`.
- Frozen head: `26315678f9ce701bb7d6ed6f58fef6cf181cecb9`; tree `b8af0f8fb444cd2540f8f893d619341f2ba93feb`.
- Preregistration SHA-256: `893631a078d8c6bc6482cd91a7e0a771399cf87c5c2d0d332faf3e53938cd1fe`.
- Artifact: `C:\Dev\ZackBot_cowork_data\ar2_forecast_c93\ar2-forecast-long-dev.json`.
- Artifact SHA-256: `f28a916f432c029ab988e33916a33d65a38a6c792e2e1436d7debbce5908dc19`.
- Canonical packet SHA-256 (independently reproduced): `020ae4086cb29a644cd7074e531203c4ec1d9e976800e7cbc618e77d6e3c08aa`.

The frozen CORE-20 development run closed 439 long trades over all 18 symbols: 336 above and 103 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$35.372048. Fees were $60.524379 and actual signed funding was $1.658071,
leaving net P&L -$97.554497, net -20.000767R (mean -0.045560R), PF 0.826294, and max drawdown 30.369567R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The forecast hypothesis is negative before costs; no order, window, threshold, or regularization change can rescue this
frozen cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed post-2026-09-28 holdout was not accessed.
