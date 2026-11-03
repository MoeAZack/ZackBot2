# Candidate 29 / EMA-supertrend short Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 29, the existing `sig_ema_st` function's previously off-registry short cell, cloned as
`ema_st_short_c29`. The frozen cell was evaluated once at exact head `8b982e87fc0a2de98d8f1032343848495e64d3ca`.

## Creditable development result

| Trades | Symbols | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---|---|---|
| 28 | 8 | +2.52R | +0.090R | 1.198 | 6.01R | 12 / 16 | economics/breadth/DD PASS; sample/regime FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\emastshort4h_c29\ema-st-short-dev.json`  
Packet SHA-256: `f823ea97ab548c5888d25880ccd2197f7328ee729af750107cd7775dd40d2038`

Every frozen development gate was inspected before folds. The candidate had only 28 closes versus 100 required and only
12/16 closes in the two BTC regimes versus 20 each. No fold was run and the sealed holdout stayed untouched. Positive
small-sample economics cannot lower gates, extend dates, pool sides or change management.

ROADMAP FIT: next genuinely untested emitted board cell after candidate 28; tested-candidate ledger update.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
