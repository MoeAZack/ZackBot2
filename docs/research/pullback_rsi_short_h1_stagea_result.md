# Candidate 23 / H1 pullback-RSI short development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 23, queued H1 `pullback_rsi_short_h1`, one short cell. The frozen cell was evaluated once on
CORE-20 development at exact head `d9079f11f6a5e7810f75651915e493c2322f4b99`.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| Short | 51 | 9 | +$57.37 | $5.26 | -$1.71 | +$53.82 | +11.10R | +0.218R | 1.448 | 9.20R | 21 / 30 | economics PASS; breadth PASS; regimes PASS; DD PASS; sample FAIL (51 < 100) | parked/rejected |

No chronological fold or holdout was opened. Extending the window, lowering the sample gate, changing the RSI threshold,
pooling with the existing long cell, selecting symbols/regimes, or changing management would be post-result rescue.

Development packet:

- `C:\Dev\ZackBot_cowork_data\prs4h_h1\prs-short-dev.json`
- SHA-256: `0a3c14f05938ce60dee56f521fd5dc4e21040b315b0d651c9407b8f86f887c78`

ROADMAP FIT: queued H1 re-evaluation and tested-candidate ledger update.  
DEFERRED: H2-H4, folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
