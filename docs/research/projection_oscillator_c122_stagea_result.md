# Candidate 122 Projection Oscillator Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/projection-oscillator-c122`.
- Frozen head: `bd621be41503cf1c42b34c9262557dab7eac9738`; tree `05cfc193be003e2fe9767b18b7340037d8298071`.
- Preregistration SHA-256: `b803968b75158e881e2868583003c7051e2c308967e19655f2c39c980ce87c73`.
- Artifact: `C:\Dev\ZackBot_cowork_data\projection_oscillator_c122\projection-long-dev.json`.
- Artifact SHA-256: `aabd363bdf8b858d6c6128360d07caed08275a2256fca72d28c1f1cdce449929`.
- Canonical packet SHA-256 (independently reproduced): `aa824c0934c1c97ed1619571b320ce027ba8a01dd5474162fbef24de68ad4076`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 2,329 long trades over all 18 symbols: 1,465 above and 864 below the BTC
4h EMA200 regime boundary. Pre-cost gross was -$16.963182. Fees were $323.730217 and actual signed funding was
$11.358549, leaving net P&L -$352.051948, net -107.142913R (mean -0.046004R), PF 0.834503, and max drawdown
126.007191R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The projected-channel location transition has no edge before costs and fails payoff/risk materially; no window,
threshold, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
