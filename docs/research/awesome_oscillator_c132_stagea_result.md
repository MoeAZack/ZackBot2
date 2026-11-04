# Candidate 132 Awesome Oscillator Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/awesome-oscillator-c132`.
- Frozen head: `9e03c53946c0404b75dcd7f68ee6987e83dcceb3`; tree `abd1275285f03ce8f0d9891c983c1b3d9bb3459d`.
- Preregistration SHA-256: `25f09db5328c807565bbbf026dbfa6234d5da51b05d6ebd3fd277bd8967df135`.
- Artifact: `C:\Dev\ZackBot_cowork_data\awesome_oscillator_c132\awesome-oscillator-long-dev.json`.
- Artifact SHA-256: `31145c255a3eb1d655d5ff04dad7713e5bafde4e5be15c2323a9b1470b9d4ece`.
- Canonical packet SHA-256 (independently reproduced): `247d8d64eba7495c1f8c5eaea7817475a142726f0461c62fa84bdc5d898626fe`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 645 long trades over all 18 symbols: 387 above and 258 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was $65.595211. Fees were $141.094816 and actual signed funding was $16.795663,
leaving net P&L -$92.295268, net -10.060511R (mean -0.015598R), PF 0.951240, and max drawdown 52.254041R. Exit counts
were 184 signal, 223 stop, 110 time, and 128 target.

Passed gates: pre-cost gross positive, sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: mean net
R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK. The
positive gross edge is erased by frozen realistic costs and payoff/risk remains inadequate; no window, filter, threshold,
or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
