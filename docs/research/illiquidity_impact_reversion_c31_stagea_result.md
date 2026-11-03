# Candidate 31 / illiquidity-impact reversion long Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 31, new frozen `illiquidity_impact_reversion_long_1h` long cell. It was evaluated once at
exact head `4cc66aacfb46e3fb956ddc5d1e01aa77195a10a4` after an all-branch duplicate search found no prior family or result.

## Creditable development result

| Trades | Symbols | Gross P&L | Fees | Funding | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 50 | 15 | +$4.40 | $8.26 | $0.17 | -0.46R | -0.009R | 0.960 | 7.42R | 27 / 23 | gross/breadth/regime/DD PASS; net/PF/sample FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\illiqrev1h_c31\illiqrev-long-dev.json`  
Packet SHA-256: `320d660e7cfe46da5071d9b151918524fe36b540067c3fc2907c41c90ab2988c`

Every frozen development gate was inspected before folds. Positive gross impact reversion did not cover realistic fees,
and only 50 trades closed versus 100 required. No fold was run and the sealed holdout stayed untouched. No impact/shock
threshold, volume floor, short inversion, management change, date extension or gate relaxation can rescue this cell.

ROADMAP FIT: orthogonal post-inventory liquidity-impact scout and tested-candidate ledger update.  
DEFERRED: short cell, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
