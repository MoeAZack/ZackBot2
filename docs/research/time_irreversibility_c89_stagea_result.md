# Candidate 89 nonlinear time-irreversibility Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/time-irreversibility-c89`.
- Frozen head: `2185246e83214a5114cb8e1ee1501288774c84c9`; tree `3c9c30689fd732e0461775561f0f6be79231acf0`.
- Preregistration SHA-256: `2dc2f87dc052ed58a514e0a9dd47495076c49b101872f41053e43ef172649bc1`.
- Artifact: `C:\Dev\ZackBot_cowork_data\time_irreversibility_c89\time-irreversibility-long-dev.json`.
- Artifact SHA-256: `cc12215ab3f0eda03fa264ccce8cd89fe0aa09e42b2354ebe8bafeb5fbd38e87`.
- Canonical packet SHA-256 (independently reproduced): `67547a6075f6827f58200f839f3900482a3492a2973497e99c8f87fb58112344`.

The frozen CORE-20 development run closed 943 long trades over all 18 symbols: 620 above and 323 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was +$44.128089. Fees were $196.755070 and actual signed funding was $16.226597,
leaving net P&L -$168.853578, net -30.669656R (mean -0.032523R), PF 0.919022, and max drawdown 69.317276R.

Passed gates: pre-cost gross positive, sample >=100, symbols >=8, and >=20 closes per regime. Failed gates: mean net R
positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK. The
positive gross edge did not survive realistic costs, and the risk ceiling failed materially. No rescue or parameter
tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed post-2026-09-28 holdout was not accessed.
