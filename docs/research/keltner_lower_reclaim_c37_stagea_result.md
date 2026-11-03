# Candidate 37 / Keltner lower reclaim long Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 37, new frozen `keltner_lower_reclaim_long_1h` long cell. It was evaluated once at exact head
`bf2c9d284fa8efd6295f688b304fa838d0a58075` after an all-ref/history/ledger search found no prior Keltner or EMA/ATR
channel-reclaim strategy, preregistration, packet or result.

## Creditable development result

| Trades | Symbols | Gross P&L | Fees | Funding | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 485 | 18 | -$128.40 | $77.28 | $2.34 | -48.79R | -0.101R | 0.766 | 55.93R | 228 / 257 | sample/breadth/regime PASS; gross/net/PF/DD FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\keltner1h_c37\keltner-reclaim-long-dev.json`  
Packet SHA-256: `320109c97680bcfe72465f470250e01b8456fc6ee86ed15421b635a40c7ea3ac`

Every frozen development gate was inspected before folds. The cell was strongly negative before costs and breached the
drawdown cap by a wide margin, so no fold was run and the sealed holdout stayed untouched. Changing the band/window,
volume admission, short inversion, management, chronology or gates would be a new trial and cannot rescue it.

ROADMAP FIT: distinct frequent volatility-channel mean-reversion scout and tested-candidate ledger update.  
DEFERRED: Keltner variants, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
