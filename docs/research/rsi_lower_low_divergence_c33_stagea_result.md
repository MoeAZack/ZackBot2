# Candidate 33 / RSI lower-low divergence long Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 33, new frozen `rsi_lower_low_divergence_long_1h` long cell. It was evaluated once at exact
head `f88fef3e640f2845a8451e9db8598bf866762c7e` after an all-ref/history/ledger search found no prior RSI or momentum
divergence strategy, preregistration, packet or result.

## Creditable development result

| Trades | Symbols | Gross P&L | Fees | Funding | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 252 | 18 | -$133.47 | $35.73 | $0.42 | -38.92R | -0.154R | 0.667 | 45.01R | 96 / 156 | sample/breadth/regime PASS; gross/net/PF/DD FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\rsidiv1h_c33\rsi-div-long-dev.json`  
Packet SHA-256: `84d62a021115280def007cda7e60e13a85bf01e414415592d2dcda832148a9c9`

Every frozen development gate was inspected before folds. The cell was strongly negative before costs and incurred a
45R drawdown, so no fold was run and the sealed holdout stayed untouched. Changing the divergence/window/separation,
volume or candle confirmation, short inversion, management, window or gates would be a new trial and cannot rescue it.

ROADMAP FIT: distinct frequent mean-reversion scout and tested-candidate ledger update.  
DEFERRED: alternate divergence definitions, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
