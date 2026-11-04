# Candidate 123 McGinley Dynamic crossover Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/mcginley-c123`.
- Frozen head: `e0e0d39279d840c8764aab6ff75fb8dc85100d07`; tree `b6ebac34bb883f65807c40e29511b3353b7afbb2`.
- Preregistration SHA-256: `7328869dacffb72bb1243fc693ca4fb79eafc3e99d0e714e2d9298476b5edf73`.
- Artifact: `C:\Dev\ZackBot_cowork_data\mcginley_c123\mcginley-long-dev.json`.
- Artifact SHA-256: `188e80be9f1f4460690ae9e1561b39e05d03319d6e6c9f043247db8acc222bea`.
- Canonical packet SHA-256 (independently reproduced): `48405d33ad4a885dc36518efd30a42a94e8d9fb2edb2fe93d732ecde68dccddb`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,872 long trades over all 18 symbols: 1,131 above and 741 below the BTC
4h EMA200 regime boundary. Pre-cost gross was -$43.609876. Fees were $282.606416 and actual signed funding was
$15.025648, leaving net P&L -$341.241940, net -97.727308R (mean -0.052205R), PF 0.837136, and max drawdown
120.240708R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The nonlinear price-ratio adaptive crossover has no edge before costs and fails payoff/risk materially; no period,
constant, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
