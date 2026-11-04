# Candidate 110 FRAMA crossover Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/frama-c110`.
- Frozen head: `d70df5216ab191d2427fe9e1c4bb05212b8f71cd`; tree `5a2c9096ec3e8dd0caaf129c3feaa0b7c3099278`.
- Preregistration SHA-256: `e6d6fe1199f1597130dd6ea5857866692a64a46c2bf9756d4f50e7c1b1160742`.
- Artifact: `C:\Dev\ZackBot_cowork_data\frama_c110\frama-long-dev.json`.
- Artifact SHA-256: `e6d6a18c386b214fb8ab83712ee69fa3903cc44d50d8a7d03acd68762a42f004`.
- Canonical packet SHA-256 (independently reproduced): `876513bee8a9584f71bb3298f22b3a5ad4809ee6f95de6a7d4f99514604b2613`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,944 long trades over all 18 symbols: 1,180 above and 764 below the BTC
4h EMA200 regime boundary. Pre-cost gross was +$43.592418. Fees were $322.056827 and actual signed funding was
$16.388756, leaving net P&L -$294.853164, net -73.899395R (mean -0.038014R), PF 0.878807, and max drawdown
105.334796R.

Passed gates: pre-cost gross positive, sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The fractal-adaptive crossover is too turnover-heavy to survive costs and fails payoff/risk materially; no window,
alpha bound, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
