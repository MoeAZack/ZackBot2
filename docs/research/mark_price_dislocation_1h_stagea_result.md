# MPD-1H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `mark_price_dislocation_1h` cells were evaluated once on CORE-20 development at exact head
`44c1edb48c1bf0277fad22eec711f1ef2f88515e`. Both met sample, breadth and regime-count gates. Neither met the frozen
economic gate, so no chronological fold was opened.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Max DD | Regimes above/below | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Long | 342 | 17 | +$63.89 | $64.11 | +$0.25 | -$0.47 | +0.93R | +0.003R | 0.999 | 10.01R | 176 / 166 | reject: costs, net and PF |
| Short | 170 | 16 | -$22.71 | $25.35 | -$0.15 | -$47.91 | -9.57R | -0.056R | 0.775 | 18.45R | 142 / 28 | reject: pre-cost, net, PF and DD |

The long cell is a direct cost-resilience failure: its gross edge is smaller than fees before funding. Increasing the
basis threshold, selecting hours/symbols/regimes, pooling sides or changing exits after inspection would be post-result
rescue and is prohibited.

Packets under `C:\Dev\ZackBot_cowork_data\mpd1h_stagea\`:

- long: `b59d18ffdd7332da6c2db2445d2bdfcdac8a6c29d01ee3357e8d8945278ddafb`
- short: `964025769615cd4968c99b08ae5c87140abb69496091b8c4c46ee5ef6d4f02ba`

ROADMAP FIT: independent exchange-microstructure candidate 17.  
DEFERRED: folds, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
