# Candidate 29 / EMA-supertrend short Stage-A result

Status: **VOID / NON-CREDITABLE DUPLICATE / NO PAPER AUTHORITY**

Candidate 29 cloned the existing `sig_ema_st` short cell as `ema_st_short_c29` and evaluated it at exact head
`8b982e87fc0a2de98d8f1032343848495e64d3ca`. A subsequent all-branch audit found the exact short cell had already been
evaluated in the older review-only breadth batch `2026-10-15_core20_c8c7b92_batch6`. Because the earlier evidence was on
another Git branch and absent from the current branch's visible ledger files, the duplicate was not detected before the
run. This new run is preserved for audit but is **void and non-creditable**; it cannot add a trial or support a claim.

## Creditable development result

| Trades | Symbols | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---|---|---|
| 28 | 8 | +2.52R | +0.090R | 1.198 | 6.01R | 12 / 16 | duplicate; sample/regime also fail | void |

Packet: `C:\Dev\ZackBot_cowork_data\emastshort4h_c29\ema-st-short-dev.json`  
Packet SHA-256: `f823ea97ab548c5888d25880ccd2197f7328ee729af750107cd7775dd40d2038`

No fold was run and the sealed holdout stayed untouched. The packet cannot be used, pooled, relabelled or counted because
the exact rule was already evaluated. Future queue selection must search all Git branches for evidence paths before a run.

ROADMAP FIT: duplicate-detection correction and tested-candidate ledger integrity.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
