# Candidate 32 / inside-bar breakout long Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 32, new frozen `inside_bar_breakout_long_1h` long cell. It was evaluated once at exact head
`49fd8bc4b3e8f0acf64e48a69cd7cee8077cba16` after an all-ref/history duplicate search found no prior inside/mother-bar
strategy, preregistration, packet or result.

## Creditable development result

| Trades | Symbols | Gross P&L | Fees | Funding | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 196 | 18 | -$16.70 | $35.83 | $3.59 | -10.33R | -0.053R | 0.880 | 19.91R | 128 / 68 | sample/breadth/regime PASS; gross/net/PF/DD FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\ibb1h_c32\ibb-long-dev.json`  
Packet SHA-256: `7d531feb81bc1fc8e77c247babb36688be9cb71f25b15ef1ac82f658a82f6fe6`

Every frozen development gate was inspected before folds. The cell was negative before costs and deteriorated further
after realistic taker fees, adverse slippage and actual funding. No fold was run and the sealed holdout stayed untouched.
Changing the mother/inside thresholds, trend or volume gates, short inversion, management, window or gates would be a new
trial and cannot rescue candidate 32.

ROADMAP FIT: next orthogonal frequent-strategy scout and tested-candidate ledger update.  
DEFERRED: short cell, alternate pattern definitions, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
