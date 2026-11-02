# RSV-1H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `realized_skew_valuation_1h` cells were evaluated once on CORE-20 development at exact head
`a4e2be8d9aedff0b4de915d5599346780d6b6210`. Both met the sample, breadth and regime-count gates. Neither met frozen
economic and drawdown gates, so no chronological fold was opened.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Max DD | Regimes above/below | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Long | 178 | 17 | -$54.45 | $37.02 | +$3.80 | -$95.27 | -18.03R | -0.101R | 0.805 | 34.84R | 98 / 80 | reject: pre-cost, net and DD |
| Short | 231 | 18 | +$14.67 | $29.71 | -$2.69 | -$12.34 | +0.40R | +0.002R | 0.977 | 41.34R | 171 / 60 | reject: net, PF and DD |

The small positive short gross/R observation is not a survivor: fees exceed gross P&L, PF is below one and drawdown is
nearly three times the cap. Changing the skew threshold/window, selecting regimes or symbols, pooling sides or changing
exits after inspection would be post-result rescue.

Packets under `C:\Dev\ZackBot_cowork_data\rsv1h_stagea\`:

- long: `3954d3052d3c14fda3e6ec8070e96317824346be3d04d5e209182d158af8e1ee`
- short: `21fd58d1ccac4fa6d58f40cffdbea663ac44515dd694f1f99127cd00b85cad69`

ROADMAP FIT: lower-turnover distribution-shape candidate 16.  
DEFERRED: folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
