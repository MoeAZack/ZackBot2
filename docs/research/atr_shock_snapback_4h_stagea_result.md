# ASS-4H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `atr_shock_snapback_4h` cells were evaluated once on CORE-20 development at exact head
`5ba66427970b030eec87b0a3d1167ecad4363760`. Both small samples are positive, but neither comes remotely close to the
preregistered 80-trade/eight-symbol or per-regime sample gates. No chronological fold may open. No rule changed.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Regime counts | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Long | 11 | 6 | +$8.63 | $0.87 | +$0.15 | +$7.61 | +1.54R | +0.140R | 1.564 | above 9 / below 2 | reject: sample, breadth, regimes |
| Short | 5 | 5 | +$12.35 | $0.59 | -$0.04 | +$11.80 | +2.34R | +0.468R | undefined (no losses) | above 2 / below 3 | reject: sample, breadth, regimes |

The positive point estimates cannot be treated as evidence at 11 and five observations. Lowering the 1.5 ATR shock,
removing the midpoint reclaim, pooling directions, or selecting only the winning symbols would be post-result rescue.

Packets under `C:\Dev\ZackBot_cowork_data\ass4h_stagea\`:

- long: `c525b7a26253378bea18bc1e2345309f9caf473a97f146ffa7d08a2843d69be3`
- short: `07037f09d19ec49b03e9a8fb40d8240e927e56f072d2c0fbe2b85905a4235596`

ROADMAP FIT: simple 4h slow-trend snapback complement.  
DEFERRED: folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
