# Candidate 26 / H4 EMA-momentum-long Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 26, queued H4 `ema_mom_long_h4`, one long management cell. The frozen cell was evaluated once
at exact head `35161ef3a79e636b64c8ee9bc4d3cc32bee0d89f`.

## Creditable development result

| Trades | Symbols | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---|---|---|
| 20 | 7 | +6.13R | +0.306R | 2.159 | 2.49R | 19 / 1 | economics/DD PASS; sample/breadth/regime FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\emamom4h_h4\ema-mom-long-dev.json`  
Packet SHA-256: `40bcc8c573e4e9f3a99c81d711547d644bd04b1002f72e3a433317bd163ca9ca`

Every frozen development gate was inspected before launching folds. The candidate failed >=100 trades, >=8 symbols and
>=20 closes in each BTC regime, so no chronological folds were run and the sealed holdout stayed untouched. Its positive
small-sample economics cannot rescue or relabel it. No threshold, date, side, symbol, regime or management change is
allowed.

ROADMAP FIT: queued H4 re-evaluation and tested-candidate ledger update.  
DEFERRED: further scouting, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
