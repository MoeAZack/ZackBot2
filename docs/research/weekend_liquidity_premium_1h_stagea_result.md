# WLP-1H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `weekend_liquidity_premium_1h` cells were evaluated once on CORE-20 development at exact head
`55879c71d803961158bb54d2dac4e150d94f4778`. Both met trade-count and BTC-regime-count gates, but simultaneous signals
filled the four portfolio slots and therefore reached only four symbols versus the frozen eight-symbol breadth minimum.
No chronological fold was opened.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Max DD | Regimes above/below | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Long | 188 | 4 | -$30.51 | $35.76 | +$9.22 | -$75.50 | -13.99R | -0.074R | 0.816 | 30.70R | 108 / 80 | reject: economics, DD and breadth |
| Short | 190 | 4 | +$150.48 | $40.39 | -$8.32 | +$118.40 | +25.05R | +0.132R | 1.259 | 14.52R | 108 / 82 | park: 4 < frozen 8 symbols |

The short observation is strong but concentrated in BTC, ETH, SOL and ZEC because all symbols signal simultaneously
against a four-position portfolio. Raising capacity, staggering or rotating symbols, selecting those four symbols,
pooling boundaries/sides or changing exits after inspection would be post-result rescue. The cell remains parked as a
failed breadth observation in the multiple-testing ledger.

Packets under `C:\Dev\ZackBot_cowork_data\wlp1h_stagea\`:

- long: `72db91167f54c2d37188e8fa11ba562d3c5cc007e035c110f64d7418133419a5`
- short: `05a67c6cd0318f9159274fb5e2c0e0e80efaec636e3a11cdb01f849a45612d02`

ROADMAP FIT: low-turnover calendar-risk candidate 18.  
DEFERRED: folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
