# Candidate 90 magnitude-aware Sample Entropy Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/sample-entropy-c90`.
- Frozen head: `04244154f31de2365721fc90da434937ba89b813`; tree `f7c7259196635955ce3ca13123d5dbdd0e75991e`.
- Preregistration SHA-256: `5b90a42c801595a97708c87c093ca2287f43d792b924dcf8503ffe17fb779829`.
- Artifact: `C:\Dev\ZackBot_cowork_data\sample_entropy_c90\sample-entropy-long-dev.json`.
- Artifact SHA-256: `ce23f731b330cffda966eede2b8384af79e84817759c38419a90c5ebede64916`.
- Canonical packet SHA-256 (independently reproduced): `7238aff6bc7b258522a7dcd53e0693d4156240c2a0a6d63d28c8ec592b88cac4`.

The frozen CORE-20 development run closed 6 long trades over 4 symbols: 2 above and 4 below the BTC 4h EMA200 regime
boundary. Pre-cost gross was +$1.133960. Fees were $0.716937 and actual signed funding was $0.052416, leaving net P&L
+$0.364607, net +0.104153R (mean +0.017359R), PF 1.028883, and max drawdown 1.342782R.

Passed gates: pre-cost gross positive, mean net R positive, and max drawdown <=15R. Failed gates: PF >1.05, sample
>=100, symbols >=8, and >=20 closes in each regime. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The six-trade result is not inferential evidence and its positive net value cannot override the frozen sample/breadth
requirements. No rescue or parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed post-2026-09-28 holdout was not accessed.
