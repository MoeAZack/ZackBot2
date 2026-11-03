# Candidate 25 / H3 exp-trend-long Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 25, queued H3 `exp_trend_long_h3`, one long cell. The frozen cell was evaluated once at exact
head `6ccea60938e28685938f2ef30a196ec88a253dc3`.

## Creditable development result

| Trades | Symbols | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---|---|---|
| 20 | 7 | +4.25R | +0.212R | 1.805 | 2.89R | 19 / 1 | economics/DD PASS; sample/breadth/regime FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\exptr4h_h3\exp-trend-long-dev.json`  
Packet SHA-256: `8f40458d6ee4eb607af27d982bfd03c4596d27620f782d65294c310d4942616f`

Every frozen development gate was inspected before launching folds. The candidate failed >=100 trades, >=8 symbols and
>=20 closes in each BTC regime, so no chronological folds were run and the sealed holdout stayed untouched. Positive
small-sample economics cannot rescue or relabel it. No threshold, date, side, symbol, regime or management change is
allowed.

ROADMAP FIT: queued H3 re-evaluation and tested-candidate ledger update.  
DEFERRED: H4, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
