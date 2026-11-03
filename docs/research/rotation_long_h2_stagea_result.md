# Candidate 24 / H2 rotation-long Stage-A result

Status: **HISTORICAL STAGE-A REJECTION; RECLASSIFIED TO ELEVATED-DD EXTENDED VALIDATION / NO PAPER AUTHORITY YET**

Owner ruling on 3 November 2026 removed drawdown as a stand-alone economic rejection. The frozen result below is not
rewritten: it failed the preregistered 15R gate. Under the prospective risk policy it advances to chronological,
independently reproduced validation with its 18.60R drawdown disclosed and a maximum future initial PAPER risk
multiplier of 0.806x. See `PROFIT_AND_DRAWDOWN_PROMOTION_POLICY.md`.

Ledger identity: candidate 24, queued H2 `rotation_long_h2`, one long cell. The frozen cell was evaluated at exact head
`06e821bbd5f344492c658f6037fe499f8c964134`.

## Creditable development result

| Trades | Symbols | Net R | Mean R | PF | Max DD | Regimes above/below | Gates | Verdict |
|---:|---:|---:|---:|---:|---:|---|---|---|
| 174 | 9 | +30.85R | +0.177R | 1.464 | 18.60R | reported gate PASS | economics/sample/breadth/regime PASS; DD FAIL (18.60 > 15R) | rejected |

Packet: `C:\Dev\ZackBot_cowork_data\rot4h_h2\rot-long-dev.json`  
SHA-256: `474ba2eddf2e046bc4b8652fcd17b5182d396c20cb0e3537a721a8acb328ee66`

## Gate-order deviation

Three chronological fold jobs were launched after the summary line showed development economics/breadth/regime passing
but before the development packet's 18.60R drawdown was inspected. Because the frozen development DD gate had already
failed, those fold packets are **VOID / NON-CREDITABLE** and cannot rescue or relabel this candidate. They are preserved
for audit rather than deleted: WF1 `a2001aeb35f5e6092faf6d3f7ba985af01cfff8e61377f32b5715821e8bd6eb6`,
WF2 `07e529cf20ec1f54d1889ae019030c05b1f11f9bf2e4d0fcab4bdcedc2728fe5`, and WF3
`11f35082be354fc051d6bf7d022afbe21b81a7c6cb6c6ae97d607beaba1abafe`. Their combined result also has
15.38R max drawdown, above the same frozen cap. The sealed holdout was never opened.

No drawdown relaxation, parameter change, symbol/regime selection, pooling or management change is allowed.

ROADMAP FIT: queued H2 re-evaluation and tested-candidate ledger update.  
DEFERRED: H3-H4, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
