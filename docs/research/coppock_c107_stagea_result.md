# Candidate 107 Coppock-curve zero-cross Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/coppock-c107`.
- Frozen head: `84493614562fe02064eff8a0b27f8cdb9cedc40f`; tree `4753db85455e1d7b50421235fc9c6790f5301fd3`.
- Preregistration SHA-256: `bf86d4a128e8cafab0521725cbaf85365cbddb2caf68826a2532e75b374211c2`.
- Artifact: `C:\Dev\ZackBot_cowork_data\coppock_c107\coppock-long-dev.json`.
- Artifact SHA-256: `0d511a964433407d39c3cdcef158f7dbc67a3f12eb703a838f0125c3b41697ba`.
- Canonical packet SHA-256 (independently reproduced): `1a3d8907a53ed8f984aa876f2c73cb0d62277ffd0bbbf17c2e07dc18f1ca7256`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 693 long trades over all 18 symbols: 425 above and 268 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$101.352415. Fees were $109.897739 and actual signed funding was
$12.520544, leaving net P&L -$223.770699, net -48.752222R (mean -0.070350R), PF 0.848597, and max drawdown
67.914986R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The weighted dual-horizon rate-of-change recovery has no edge before costs and fails payoff/risk materially; no
lookback, threshold, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
