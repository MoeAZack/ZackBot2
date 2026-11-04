# Candidate 124 Ease of Movement Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/ease-of-movement-c124`.
- Frozen head: `71a244e0e7c663b08477055d670477d7f369bc94`; tree `685e90c8c31cc3e7aae9187b4f7c52a99d429cf0`.
- Preregistration SHA-256: `022ebd354cb80d7e241c5f130e5efe852463a80364cd1e68ce06bd87a226ae1d`.
- Artifact: `C:\Dev\ZackBot_cowork_data\ease_of_movement_c124\eom-long-dev.json`.
- Artifact SHA-256: `21e17b36b78a4a4e1ed36f8b1b450a8bbf8f61fa7c4ce79c33176e5dfb451b73`.
- Canonical packet SHA-256 (independently reproduced): `808d9cdb13416cc1eb38012889a91b748d44394e62e29210974d357d1f591946`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,262 long trades over all 18 symbols: 785 above and 477 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$114.821831. Fees were $182.944633 and actual signed funding was $14.824416,
leaving net P&L -$312.590881, net -83.582100R (mean -0.066230R), PF 0.820409, and max drawdown 95.538350R. Exit
counts were 882 signal, 200 stop, 36 time, and 144 target.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen range/liquidity-efficiency zero-cross has no edge before costs and fails payoff/risk materially; no window,
filter, normalization, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
