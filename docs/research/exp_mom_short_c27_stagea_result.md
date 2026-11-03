# Candidate 27 / cold-coin momentum short Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 27, exact existing `exp_mom_short` cloned as `exp_mom_short_c27`, one short cell. The frozen
cell was evaluated once at exact head `e3533295cd773e91aa766dc2f00b82266388e09a`.

## Creditable development result

| Trades | Symbols | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---|---|---|
| 65 | 9 | +5.13R | +0.079R | 1.174 | 5.55R | 35 / 30 | economics/breadth/regime/DD PASS; sample FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\expmomshort4h_c27\exp-mom-short-dev.json`  
Packet SHA-256: `5d62f6b23a4547208f98cceffa8672747e8f0a2725de5b3962e154598a13a0cc`

Every frozen development gate was inspected before launching folds. The candidate produced 65 closes versus the frozen
minimum of 100, so no chronological folds were run and the sealed holdout stayed untouched. The otherwise positive
development result cannot lower the gate, extend the dates, pool another side or rescue the candidate.

ROADMAP FIT: first unused board-cell scout after queued H1-H4; tested-candidate ledger update.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
