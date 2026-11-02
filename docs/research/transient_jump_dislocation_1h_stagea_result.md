# TJD-1H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `transient_jump_dislocation_1h` cells were evaluated once on CORE-20 development at exact head
`5cc2d5bcbb171c8feed4aa89669b0a2f0d6bc6d7`. Both achieved high asynchronous sample density, all-symbol breadth and
BTC-regime coverage, but failed the frozen net-economic and drawdown gates. No chronological fold was opened.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Max DD | Regimes above/below | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Long | 599 | 18 | +$45.85 | $93.91 | +$1.17 | -$49.23 | -7.89R | -0.013R | 0.940 | 30.65R | 427 / 172 | reject: costs, net, PF and DD |
| Short | 740 | 18 | +$33.84 | $106.11 | -$1.41 | -$70.87 | -12.85R | -0.017R | 0.923 | 23.70R | 460 / 280 | reject: costs, net, PF and DD |

Both sides have positive pre-cost gross P&L, but neither pays realistic turnover. Increasing the jump/participation
threshold, selecting symbols/times/regimes, pooling sides or changing exits after inspection would be post-result rescue.

Packets under `C:\Dev\ZackBot_cowork_data\tjd1h_stagea\`:

- long: `c8bbf36c5213b640384b281ca51a135698e2689a99e461a2182d1e54e6c158a6`
- short: `8b8778f38d25515e3543f2ed69d463e8b54753b3fc94414c3502c1f64d9ac427`

ROADMAP FIT: asynchronous archived-microstructure candidate 19.  
DEFERRED: folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
