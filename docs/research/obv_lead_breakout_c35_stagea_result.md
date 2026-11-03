# Candidate 35 / OBV lead breakout long Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 35, new frozen `obv_lead_breakout_long_1h` long cell. It was evaluated once at exact head
`0f8260ace190fd4324ede96116ffb7eac2c12331` after an all-ref/history/ledger search found no prior OBV or cumulative
signed-volume strategy, preregistration, packet or result.

## Creditable development result

| Trades | Symbols | Gross P&L | Fees | Funding | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 523 | 18 | -$94.41 | $88.53 | $7.33 | -42.62R | -0.081R | 0.807 | 54.85R | 345 / 178 | sample/breadth/regime PASS; gross/net/PF/DD FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\obvlead1h_c35\obv-lead-long-dev.json`  
Packet SHA-256: `2a6d56bf5e1af556f0a89fed5d5472396a5fd2e7bd553161cac1bf93a5c1c413`

Every frozen development gate was inspected before folds. The cell was negative before costs and breached the drawdown
cap by a wide margin, so no fold was run and the sealed holdout stayed untouched. Changing the OBV/price window, volume
admission, price-lag rule, short inversion, management, window or gates would be a new trial and cannot rescue it.

ROADMAP FIT: distinct frequent volume-state continuation scout and tested-candidate ledger update.  
DEFERRED: OBV variants, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
