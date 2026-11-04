# Candidate 135 Williams Alligator Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/alligator-c135`.
- Frozen head: `502d63d6a7651a815c0fe860e1d945bbe8432cf5`; tree `a495df1a2104ac3dd4826eb8f6566ad2b54a9993`.
- Preregistration SHA-256: `e578cb131b31618c9ff55933f83e1cd11686235f07bfbe25962fe5f9fe3a4bc0`.
- Artifact: `C:\Dev\ZackBot_cowork_data\alligator_c135\alligator-long-dev.json`.
- Artifact SHA-256: `45abeefc50edf804e949214478b526e2cf5d6860c1df0778e870cef511308f48`.
- Canonical packet SHA-256 (independently reproduced): `998595ef617c04f8fa8c309466e13eaa07fc54a1e99dfe0d228ce288edf3a225`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 929 long trades over all 18 symbols: 599 above and 330 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was $45.034122. Fees were $189.202057 and actual signed funding was $18.924563,
leaving net P&L -$163.092499, net -25.894949R (mean -0.027874R), PF 0.926385, and max drawdown 98.262230R. Exit counts
were 426 signal, 280 stop, 76 time, and 147 target.

Passed gates: pre-cost gross positive, sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: mean net
R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK. The
small gross edge is overwhelmed by frozen realistic costs and risk/payoff is inadequate; no period, displacement,
filter, threshold, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
