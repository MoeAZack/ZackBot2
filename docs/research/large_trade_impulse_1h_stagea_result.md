# LTI-1H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `large_trade_impulse_1h` cells were evaluated once on CORE-20 development at exact head
`ee8e06e9e5cfa399d40587885aa6556541b33db8`. Both were below the frozen sample and regime gates; neither met every
economic gate. No chronological fold was opened.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Max DD | Regimes above/below | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Long | 30 | 6 | -$5.95 | $5.06 | -$0.73 | -$10.29 | -1.94R | -0.065R | 0.845 | 3.89R | 6 / 24 | reject: economics, sample, breadth and regime |
| Short | 43 | 11 | +$8.45 | $6.43 | +$0.13 | +$1.88 | +0.63R | +0.015R | 1.022 | 6.12R | 13 / 30 | reject: PF, sample and regime |

Lowering the large-ticket multiple or move threshold, changing the baseline, selecting symbols/times/regimes, pooling
sides or changing exits after inspection would be post-result rescue.

Packets under `C:\Dev\ZackBot_cowork_data\lti1h_stagea\`:

- long: `9aff6c104441ef08cabd8131b62268ba2329c2050980c934f2965261d5b21f8c`
- short: `6cbc9fecd0f55e107576eb711cb8c162aeb4994187d280a943d1d32702070033`

ROADMAP FIT: authoritative large-ticket participation candidate 22.  
DEFERRED: folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
