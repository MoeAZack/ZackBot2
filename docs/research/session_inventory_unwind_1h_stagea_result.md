# SIU-1H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `session_inventory_unwind_1h` cells were evaluated once on CORE-20 development at exact head
`d4ee5985704b3edf44ca911e79a075635f7d0c44`. Both achieve the intended sample/breadth/regime coverage, but neither is
cost-positive. Long is negative before costs; short's small positive gross edge is overwhelmed by realistic fees. No
chronological folds may open and no hour, displacement, side or symbol is selected after inspection.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Max DD | Regime counts | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Long | 251 | 18 | -$132.55 | $54.27 | -$0.24 | -$186.58 | -45.03R | -0.179R | 0.553 | 51.32R | above 138 / below 113 | reject: pre-cost + net |
| Short | 326 | 18 | +$20.13 | $70.55 | +$0.03 | -$50.46 | -9.25R | -0.028R | 0.880 | 19.45R | above 233 / below 93 | reject: costs + net |

The short cell demonstrates why activity alone is insufficient: its 59.2% win rate does not pay turnover. Testing only
08:00 or 16:00, widening the displacement, pooling sides, or changing exits now would be post-result rescue.

Packets under `C:\Dev\ZackBot_cowork_data\siu1h_stagea\`:

- long: `1b64861e9cf53411224eb99ec466783615e1606aa9461d62cc69835fb11caf8f`
- short: `e747abd6e318c1c191b3e02bb939fb84d79585cd37b0fd3b7d372a7ace9d99e2`

ROADMAP FIT: dense 1h clock-based hypothesis after sparse candidate 13.  
DEFERRED: folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
