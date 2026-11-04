# Candidate 116 Laguerre RSI Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/laguerre-rsi-c116`.
- Frozen head: `02dbe05e002f7a443fefb5812816358388b6d0a1`; tree `fb82f36ccbc62468eb60cdb1b7d16ba97bab711d`.
- Preregistration SHA-256: `4e44e458e781c280cc886757df68364746bd0bcc76bc6a8a09a7939501892359`.
- Artifact: `C:\Dev\ZackBot_cowork_data\laguerre_rsi_c116\laguerre-long-dev.json`.
- Artifact SHA-256: `b60f80193fdb8fec913a874f0e5322708c2a2e04f4f7ff1b00d0190475266cce`.
- Canonical packet SHA-256 (independently reproduced): `b783d3394c0bef83e34f590c956761a1d58968376ee8e7d34be2df85a080605e`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,157 long trades over all 18 symbols: 710 above and 447 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$148.354554. Fees were $144.529522 and actual signed funding was
$11.104093, leaving net P&L -$303.988169, net -80.312219R (mean -0.069414R), PF 0.798714, and max drawdown
97.670714R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The recursive Laguerre directional-energy transition has no edge before costs and fails payoff/risk materially; no gamma,
threshold, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
