# Candidate 129 Hull Moving Average Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/hull-ma-c129`.
- Frozen head: `5d9c00c64475eb7e02942f048f34b398d0502231`; tree `1f554a724416821c150313bf942438f4e859708a`.
- Preregistration SHA-256: `d08c4c37f48fad2141822e428dc42a8279db71784a23a5462b35fbe434fe4eef`.
- Artifact: `C:\Dev\ZackBot_cowork_data\hull_ma_c129\hma-long-dev.json`.
- Artifact SHA-256: `7a5ee75b21b71a7d0ce0fc65b2bbd05ca1f5d8d898735cf08f9ddb0ee0e7b9e4`.
- Canonical packet SHA-256 (independently reproduced): `bcc3b31f50cf76594521e9245534a1a59400ea53458095664abc3cbe8cafb4cc`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,770 long trades over all 18 symbols: 1,148 above and 622 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$6.928928. Fees were $243.929319 and actual signed funding was $7.273618,
leaving net P&L -$258.131866, net -65.108729R (mean -0.036785R), PF 0.830483, and max drawdown 67.467559R. Exit
counts were 1,607 signal, 59 stop, 0 time, and 104 target.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen finite-weighted low-lag crossover has no edge before costs and fails payoff/risk materially; no window, filter,
or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
