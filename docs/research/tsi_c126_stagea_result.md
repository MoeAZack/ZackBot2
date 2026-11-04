# Candidate 126 True Strength Index Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/tsi-c126`.
- Frozen head: `0c24a1a99930be9d67dbace9eed5813e8f801330`; tree `126804a05308f1b9770798141a44f1ef0f77e544`.
- Preregistration SHA-256: `032f0214d5f5ed46c2d017f49c3efebe6b9b990049615f611d1fb1122703b527`.
- Artifact: `C:\Dev\ZackBot_cowork_data\tsi_c126\tsi-long-dev.json`.
- Artifact SHA-256: `885feeb427f7c72236755161bdf6ca69c3937654efd7d775c20b2853f8d537bd`.
- Canonical packet SHA-256 (independently reproduced): `daa64c2bb250b5d8cb6472d28102cd7e564834e8d210376f48026ddaec8e2112`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 789 long trades over all 18 symbols: 538 above and 251 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$92.234178. Fees were $124.574683 and actual signed funding was $9.691787,
leaving net P&L -$226.500648, net -53.309298R (mean -0.067566R), PF 0.818336, and max drawdown 74.977635R. Exit
counts were 617 signal, 71 stop, 4 time, and 97 target.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen normalized double-smoothed-momentum crossover has no edge before costs and fails payoff/risk materially; no
span, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
