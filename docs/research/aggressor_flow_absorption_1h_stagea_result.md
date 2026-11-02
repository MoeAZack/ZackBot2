# AOF-1H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `aggressor_flow_absorption_1h` cells were evaluated once on CORE-20 development at exact head
`1f7ef7cc33aa9f1ac28ac67f4a3613c8e8ba2d8b`. Both met sample, breadth and regime-count gates but were negative before
costs, after costs and beyond the drawdown cap. No chronological fold was opened.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Max DD | Regimes above/below | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Long | 665 | 18 | -$98.38 | $159.96 | +$2.36 | -$260.69 | -71.64R | -0.108R | 0.582 | 73.46R | 404 / 261 | reject: pre-cost, net, PF and DD |
| Short | 376 | 17 | -$34.37 | $102.97 | -$1.47 | -$135.88 | -30.99R | -0.082R | 0.668 | 32.21R | 196 / 180 | reject: pre-cost, net, PF and DD |

The frozen absorption interpretation failed directly. Inverting it into continuation, changing imbalance/move thresholds,
selecting symbols/times/regimes, pooling sides or changing exits now would be post-result rescue.

Packets under `C:\Dev\ZackBot_cowork_data\aof1h_stagea\`:

- long: `34f31fa3e8375ac203e5a6ea1034622f02e947d8c009adc3fe965a31a7b68c70`
- short: `e08d3146b30da9a5a0f44ad79575753d491ffba3e9db97976cd5cf0e24b4104d`

ROADMAP FIT: authoritative aggressor-flow candidate 20.  
DEFERRED: inversion, folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
