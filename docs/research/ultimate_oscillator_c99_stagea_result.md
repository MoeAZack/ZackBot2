# Candidate 99 Ultimate Oscillator bullish-cross Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/ultimate-oscillator-c99`.
- Frozen head: `8a27373fec14080e982b928ecde93aaace461263`; tree `52877c17e9f1ddbbe7792e392119420cda58928c`.
- Preregistration SHA-256: `aa5992dffc30d3d3381b6dcd5e28d24fd47132008e7dde30ce7e50c58eb81778`.
- Artifact: `C:\Dev\ZackBot_cowork_data\ultimate_oscillator_c99\ultimate-oscillator-long-dev.json`.
- Artifact SHA-256: `8c3c6d58ff8547c4c5412d19fccdfc191cb3a15f092d56473f6e3af8a8e690cc`.
- Canonical packet SHA-256 (independently reproduced): `b8280280855920b5dae5d2fd6caaa4a7fc5c9badca01a39e55ce8ee5f926e84c`.

The frozen CORE-20 development run closed 1,928 long trades over all 18 symbols: 1,187 above and 741 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$86.066710. Fees were $264.082838 and actual signed funding was $13.389746,
leaving net P&L -$363.539294, net -111.978257R (mean -0.058080R), PF 0.822125, and max drawdown 132.821994R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen gap-aware multi-horizon buying-pressure cross has no edge before costs and payoff/risk fail materially; no
horizon, level, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
