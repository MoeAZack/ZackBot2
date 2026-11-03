# Candidate 34 / three-bar exhaustion reversal long Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 34, new frozen `three_bar_exhaustion_reversal_long_1h` long cell. It was evaluated once at
exact head `f125450ddc59d02fcc5daf175801768ab65b7e32` after an all-ref/history/ledger search found no prior consecutive-bar
exhaustion strategy, preregistration, packet or result.

## Creditable development result

| Trades | Symbols | Gross P&L | Fees | Funding | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 206 | 18 | -$39.66 | $32.17 | $0.22 | -14.23R | -0.069R | 0.772 | 19.46R | 100 / 106 | sample/breadth/regime PASS; gross/net/PF/DD FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\tber1h_c34\tber-long-dev.json`  
Packet SHA-256: `34447825c10e68c964b17f7d97cc310c1edbec639a81e7cf5a719e92476461af`

Every frozen development gate was inspected before folds. The cell was negative before costs and breached the 15R
drawdown cap, so no fold was run and the sealed holdout stayed untouched. Changing streak length, drop threshold, reversal
confirmation, volume floor, short inversion, management, window or gates would be a new trial and cannot rescue it.

ROADMAP FIT: distinct frequent mean-reversion scout and tested-candidate ledger update.  
DEFERRED: alternate exhaustion definitions, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
