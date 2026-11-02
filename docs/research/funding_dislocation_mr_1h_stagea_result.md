# FDMR-1H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `funding_dislocation_mr_1h` candidate was evaluated once per side at exact code head
`b27fc114f47f162af11964d3a2d90ee31049c157` on CORE-20 development. Both sides are negative before fees and fail the
preregistered economic gate; both also miss the 100-close minimum. No chronological fold may be opened. No threshold,
symbol, side, funding treatment or management rule was changed after inspection.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Max DD | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Long | 94 | 18 | -$33.43 | $20.37 | -$0.90 | -$52.91 | -10.63R | -0.113R | 0.750 | 13.53R | reject: economic + frequency |
| Short | 63 | 18 | -$10.73 | $11.54 | -$1.50 | -$20.77 | -3.82R | -0.061R | 0.854 | 13.35R | reject: economic + frequency |

Negative funding is a receipt under the signed convention. Even with those net receipts, neither side has pre-cost or
post-cost edge. The result therefore cannot be rescued by costs, pooling sides, loosening the z threshold, or reusing the
positive win rates.

Packets are under `C:\Dev\ZackBot_cowork_data\fdmr1h_stagea\`; packet SHA-256 values:

- long development: `35b63dc36f6545f4702a1a30f24147e353ccee636692b7171b0e57cac6dcb63e`
- short development: `551a58e2fe193afa6d4ddd9bd71dcc67a3ed14cf9018ee313d31ebbae9875021`

ROADMAP FIT: actual-funding event screen after candidate 9's sparse positive cell.  
DEFERRED: folds, Stage B, holdout, PAPER/testnet, runtime settings, mainnet, and post-result rescue.
