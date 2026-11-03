# Candidate 38 / Williams %R recovery long Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 38, new frozen `williams_r_recovery_long_1h` long cell. It was evaluated once at exact head
`2637b8fbcbcd7b4a2554641b2f490616cbc5b3ee` after an all-ref/history/ledger search found no prior Williams %R or
rolling-range oscillator strategy, preregistration, packet or result.

## Creditable development result

| Trades | Symbols | Gross P&L | Fees | Funding | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 1,437 | 18 | -$175.49 | $184.37 | $6.36 | -119.00R | -0.083R | 0.758 | 125.45R | 819 / 618 | sample/breadth/regime PASS; gross/net/PF/DD FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\wrr1h_c38\williams-recovery-long-dev.json`  
Packet SHA-256: `4e38e2d710205c9e38617a3f191b6146ea9856553bb2ec9165e8932066ab6a7c`

Every frozen development gate was inspected before folds. The cell was negative before costs and breached the drawdown
cap by a wide margin, so no fold was run and the sealed holdout stayed untouched. Changing oscillator thresholds/window,
volume admission, short inversion, management, chronology or gates would be a new trial and cannot rescue it.

ROADMAP FIT: distinct frequent price-location mean-reversion scout and tested-candidate ledger update.  
DEFERRED: Williams %R variants, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
