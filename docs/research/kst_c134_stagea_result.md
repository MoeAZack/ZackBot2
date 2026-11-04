# Candidate 134 Know Sure Thing Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/kst-c134`.
- Frozen head: `97247b4d263a63c26bcc2de26c83636a165d85a2`; tree `10cd5f102dc49f71abaf5c8da9bfd66b28405b0e`.
- Preregistration SHA-256: `d1715d09ae1eb47ef72fb239c6be7cdae88036d1f2950fa2c5a7c8e568978145`.
- Artifact: `C:\Dev\ZackBot_cowork_data\kst_c134\kst-long-dev.json`.
- Artifact SHA-256: `10fe123e14d0931f802f0806da033361587bc77bb226193e5735d9c43a3ae2c9`.
- Canonical packet SHA-256 (independently reproduced): `e5cad097e4db3b6043f6f0be73bfab55c7e64eb113565cb8d264aa8f4f609fc7`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 706 long trades over all 18 symbols: 445 above and 261 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was $178.487238. Fees were $155.476522 and actual signed funding was $18.381453,
leaving net P&L $4.629263, net 10.744955R (mean 0.015219R), PF 1.002398, and max drawdown 52.171982R. Exit counts were
323 signal, 217 stop, 35 time, and 131 target.

Passed gates: pre-cost gross positive, mean net R positive, sample >=100, symbols >=8, and >=20 closes in each regime.
Failed gates: PF >1.05 and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK. The
small positive net result does not compensate for effectively flat payoff quality and excessive drawdown; no horizon,
weight, filter, threshold, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
