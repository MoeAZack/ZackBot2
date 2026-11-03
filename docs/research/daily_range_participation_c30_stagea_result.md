# Candidate 30 / daily-range participation long Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 30, new frozen `daily_range_participation_long_1h` long cell. It was evaluated once at exact
head `2dd1dfda8dca42c74122fe2e5fa41e79621c2e6e` after the defined-strategy inventory was deduplicated across all branches.

## Creditable development result

| Trades | Symbols | Gross P&L | Fees | Funding | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 324 | 18 | +$8.63 | $63.20 | $10.21 | -9.09R | -0.028R | 0.927 | 48.04R | 270 / 54 | gross/sample/breadth/regime PASS; net/PF/DD FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\drplong1h_c30\drp-long-dev.json`  
Packet SHA-256: `482f3ae0a74453410fec82c4c965195f8149bfbf41c435f6e6949061ca5cfce7`

Every frozen development gate was inspected before folds. The small positive gross price edge was overwhelmed by fees,
and drawdown exceeded the cap, so no fold was run and the sealed holdout stayed untouched. No volume/close threshold,
side inversion, turnover filter, management change or gate relaxation can rescue this cell.

ROADMAP FIT: first new scout after defined-strategy inventory exhaustion; tested-candidate ledger update.  
DEFERRED: alternate side, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
