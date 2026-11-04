# Candidate 121 Stochastic Momentum Index Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/smi-c121`.
- Frozen head: `2b2872e728489b6235e13b1bd4a78c5aa94e3309`; tree `4eeec4f10c4eb9557b6dc9602275c5820c3ee0c7`.
- Preregistration SHA-256: `de4bb5979782eb07b8b6de23831fc1b2dffb2a188acebfb1fb09dc524303bb99`.
- Artifact: `C:\Dev\ZackBot_cowork_data\smi_c121\smi-long-dev.json`.
- Artifact SHA-256: `bc77701cf8541b20cd1ca181cb23249d7a4cf0b93d1b574b26712b1ecbc57e8a`.
- Canonical packet SHA-256 (independently reproduced): `6b816b62b86eda21d5c018fe93a7b77c11461fea29403d77bd18d438749b54df`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 901 long trades over all 18 symbols: 550 above and 351 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$48.221474. Fees were $159.064521 and actual signed funding was
$15.840397, leaving net P&L -$223.126392, net -46.360138R (mean -0.051454R), PF 0.878367, and max drawdown
76.578187R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The double-smoothed range-midpoint momentum transition has no edge before costs and fails payoff/risk materially; no
range window, smoothing, threshold, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
