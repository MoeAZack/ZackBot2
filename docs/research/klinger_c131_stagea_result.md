# Candidate 131 Klinger Volume Oscillator Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/klinger-c131`.
- Frozen head: `bd9df18056cd32307ece5784f995ec71afccaea4`; tree `9d22677bf69bb0f491660a86e5228c325dfdee53`.
- Preregistration SHA-256: `a98c80a7933654c1dfe78d1d9817de50342ddd9224167cb596e15eaa88c6c6a9`.
- Artifact: `C:\Dev\ZackBot_cowork_data\klinger_c131\klinger-long-dev.json`.
- Artifact SHA-256: `57757dbdf6fc68ec22a2b70f2e0241d2a93190e31eb4159779dbcc832db12f3e`.
- Canonical packet SHA-256 (independently reproduced): `280e736e2c254fbc757d5f9640f739d066f1a3fc22e7b72d55fa1f9e2589d9aa`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,476 long trades over all 18 symbols: 912 above and 564 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was $9.054418. Fees were $258.812980 and actual signed funding was $13.857119,
leaving net P&L -$263.615681, net -63.100040R (mean -0.042751R), PF 0.879305, and max drawdown 106.055095R. Exit
counts were 1,202 signal, 136 stop, 6 time, and 132 target.

Passed gates: pre-cost gross positive, sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: mean net
R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK. The
small pre-cost gross is overwhelmed by frozen realistic costs and payoff/risk is decisively inadequate; no span, filter,
threshold, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
