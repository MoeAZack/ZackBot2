# VPI-1H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The two frozen `volume_pressure_inventory_1h` cells were evaluated once on CORE-20 development at exact head
`730318588f986e5cc52bf0cee6f245a0e48a6180`. No chronological fold was opened because neither side passed every
development gate.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Max DD | Regimes above/below | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Long | 68 | 18 | -$6.05 | $13.13 | +$0.88 | -$20.06 | -3.32R | -0.049R | 0.889 | 13.27R | 39 / 29 | reject: economics and <100 trades |
| Short | 92 | 18 | +$57.47 | $17.47 | -$1.46 | +$41.46 | +8.67R | +0.094R | 1.236 | 6.80R | 47 / 45 | reject: 92 < frozen 100 trades |

The short cell is a positive development observation, not a survivor. Lowering the 100-trade gate, reducing the frozen
10% imbalance threshold, pooling sides, extending the date range, selecting symbols or changing exits after inspection
would be post-result rescue. It remains parked unchanged for the multiple-testing ledger.

Packets under `C:\Dev\ZackBot_cowork_data\vpi1h_stagea\`:

- long: `81a74623379ebc008535f90a0ea2b641841404249cda820a23a21190614609ff`
- short: `ca3d12558455df6e74f7180969a1372321c94f62a683da14196618e62ad89b37`

ROADMAP FIT: low-turnover volume-information candidate 15 after fee-heavy candidate 14.  
DEFERRED: folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
