# Candidate 28 / rip-sell DCA short Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 28, exact existing `exp_rip_sell_dca` cloned as `rip_sell_dca_c28`, one short basket cell.
The frozen cell was evaluated once at exact head `9f517c72c9112312915a924729770e283a7cf1b0`.

## Creditable development result

| Trades | Symbols | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---|---|---|
| 61 | 7 | -0.47R | -0.008R | 0.927 | 3.19R | 29 / 32 | DD/regime PASS; pre-cost/net/PF/sample/breadth FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\ripselldca4h_c28\rip-sell-dca-dev.json`  
Packet SHA-256: `10ccc85e132fa7a0b427890654e735d23e7e7e1378beaff16895825a56cda735`

Every frozen development gate was inspected before folds. The candidate was negative before costs and after costs, with
61 closes across seven symbols, so no fold was run and the sealed holdout stayed untouched. No single-entry diagnostic,
DCA-size change, gate relaxation, date extension, side inversion or management change can rescue this frozen cell.

ROADMAP FIT: next unused existing board cell after candidate 27; tested-candidate ledger update.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
