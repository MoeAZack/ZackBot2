# BOL-1H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `btc_orderflow_leadlag_1h` cells were evaluated once on CORE-20 development at exact head
`016ea77a3a92944cb8a5eab83ad73489a084df04`. Both were negative before costs and below the frozen 100-trade sample
minimum. No chronological fold was opened.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Max DD | Regimes above/below | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Long | 53 | 16 | -$33.81 | $9.30 | +$0.18 | -$43.30 | -8.59R | -0.162R | 0.693 | 13.38R | 29 / 24 | reject: pre-cost, net, PF and sample |
| Short | 34 | 13 | -$44.49 | $5.65 | -$0.38 | -$49.76 | -9.99R | -0.294R | 0.479 | 9.99R | 18 / 16 | reject: pre-cost, net, PF, sample and regime |

Lowering the BTC flow/move thresholds, widening the target lag band, selecting symbols/times/regimes, pooling sides or
changing exits after inspection would be post-result rescue.

Packets under `C:\Dev\ZackBot_cowork_data\bol1h_stagea\`:

- long: `f00ccbfb45b0e7de79d772d1faabf700666874f3cdcd9ef67664fa8dfc20009a`
- short: `fb0950c9a16aed9c0cca2cf99df1676c9aaf7b513afe906e34ceb2aa6ad03078`

ROADMAP FIT: cross-asset information-diffusion candidate 21.  
DEFERRED: folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
