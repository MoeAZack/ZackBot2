# Candidate 117 KAMA crossover Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/kama-c117`.
- Frozen head: `237dad9ce52cd2286c23f28dfeeb49a68d572ad3`; tree `729c74affe993aea51f2fe7623dd18b1a7e0f4d5`.
- Preregistration SHA-256: `b281729499a679c02c21cf02640999158e3f3e55d466e74dbff70e19afdc93db`.
- Artifact: `C:\Dev\ZackBot_cowork_data\kama_c117\kama-long-dev.json`.
- Artifact SHA-256: `b5ba699280e14e1c324ec5b6f14102838219218c1320860ac00c1a2f64735686`.
- Canonical packet SHA-256 (independently reproduced): `14fd8791b4086b9e1b0732d55353c89cce789f00a3ee917cc74c25fbe18cc45a`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,718 long trades over all 18 symbols: 1,033 above and 685 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$34.763885. Fees were $269.806858 and actual signed funding was
$15.847945, leaving net P&L -$320.418688, net -86.302687R (mean -0.050234R), PF 0.854016, and max drawdown
117.674945R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The efficiency-adaptive crossover has no edge before costs and fails payoff/risk materially; no window, speed, filter,
or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
