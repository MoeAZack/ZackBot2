# Candidate 118 Trend Intensity Index Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/trend-intensity-c118`.
- Frozen head: `fef2ccf595791513b40d76e7592069b1f507b2a0`; tree `5a5ef85fc2b3be7b0f4895b40f906857d89adaf2`.
- Preregistration SHA-256: `42c2b176bf79b3c992d7947fef90ba51098fb26b3d73bdc90a7f3af7769acdb8`.
- Artifact: `C:\Dev\ZackBot_cowork_data\trend_intensity_c118\trend-intensity-long-dev.json`.
- Artifact SHA-256: `5a5f40bf1882f8e3b728630027c70a06c3ffff04e1b5bee5cf0f133f45871fb5`.
- Canonical packet SHA-256 (independently reproduced): `404e4a95fe2f0fb4f5d8fe0beb32c52d0635e5151518c7f5140b69ec6811d28a`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 278 long trades over all 18 symbols: 159 above and 119 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$11.216695. Fees were $52.044349 and actual signed funding was $5.034811,
leaving net P&L -$68.295855, net -10.820280R (mean -0.038922R), PF 0.909271, and max drawdown 34.299083R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The deviation-distribution trend transition has no edge before costs and fails payoff/risk materially; no window,
threshold, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
