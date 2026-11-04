# Candidate 125 Commodity Channel Index Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/cci-c125`.
- Frozen head: `5df11daaa1f575335be6a9096360290688dbbedb`; tree `dc3de47744eaad90c39e89a1c7dea2d18cb06488`.
- Preregistration SHA-256: `d7e83b2d5c1333d53abf2d9d70575cdb1dcc32866360b927aa907bc3ab2c1e63`.
- Artifact: `C:\Dev\ZackBot_cowork_data\cci_c125\cci-long-dev.json`.
- Artifact SHA-256: `b9598a3b8096155a21525adb83ef43c6a138b507f86e4e880d5e170284418a84`.
- Canonical packet SHA-256 (independently reproduced): `e61f9e4ccfb74e03988d6c2912af3eddc180343cfb1756043c7757778dd207c5`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 898 long trades over all 18 symbols: 472 above and 426 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$167.371253. Fees were $119.966840 and actual signed funding was $8.321592,
leaving net P&L -$295.659684, net -77.894198R (mean -0.086742R), PF 0.805321, and max drawdown 80.448874R. Exit
counts were 464 signal, 395 stop, 24 time, and 15 target.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen typical-price mean-deviation recovery has no edge before costs and fails payoff/risk materially; no window,
threshold, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
