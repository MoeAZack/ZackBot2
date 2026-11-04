# Candidate 108 Schaff Trend Cycle Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/schaff-cycle-c108`.
- Frozen head: `208e4e03f1d911baa47536ad8c451a8edc6c488f`; tree `6b9d773df73f2764388c60a8e61c0ea7f2ca65aa`.
- Preregistration SHA-256: `b11283c2f87842b6df12c635e8022e27413cfb0a4b6863d11688750b3b88beea`.
- Artifact: `C:\Dev\ZackBot_cowork_data\schaff_cycle_c108\schaff-long-dev.json`.
- Artifact SHA-256: `d6ab329637d65041ced3a378c44c105b0d1e1a26629bf927abeae7814380a01f`.
- Canonical packet SHA-256 (independently reproduced): `2f4fba993306045956ce0957e36b34f4ad811a0b1fdf4bbcd245712aa5908aed`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 890 long trades over all 18 symbols: 576 above and 314 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$46.428810. Fees were $140.030080 and actual signed funding was
$12.551308, leaving net P&L -$199.010198, net -39.297520R (mean -0.044155R), PF 0.879495, and max drawdown
61.120788R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The dual-stochastic MACD cycle has no edge before costs and fails payoff/risk materially; no parameter, threshold,
filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
