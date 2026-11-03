# Candidate 36 / MFI oversold recovery long Stage-A result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

Ledger identity: candidate 36, new frozen `mfi_oversold_recovery_long_1h` long cell. It was evaluated once at exact head
`0f3c5ab8c451508aa382a91d0dbd3688377e494a` after an all-ref/history/ledger search found no prior Money Flow Index
strategy, preregistration, packet or result.

## Creditable development result

| Trades | Symbols | Gross P&L | Fees | Funding | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 417 | 18 | -$169.89 | $55.33 | $0.72 | -57.08R | -0.137R | 0.622 | 60.21R | 200 / 217 | sample/breadth/regime PASS; gross/net/PF/DD FAIL | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\mfirec1h_c36\mfi-recovery-long-dev.json`  
Packet SHA-256: `264bec8ec61b9b9792ed4ad2d1f4eb0629c4152e0b8ccdac9bcf27b2ca823a11`

Every frozen development gate was inspected before folds. The cell was strongly negative before costs and breached the
drawdown cap by a wide margin, so no fold was run and the sealed holdout stayed untouched. Changing MFI thresholds or
window, volume admission, short inversion, management, chronology or gates would be a new trial and cannot rescue it.

ROADMAP FIT: distinct frequent volume-weighted mean-reversion scout and tested-candidate ledger update.  
DEFERRED: MFI variants, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
