# Repository cleanup — 12 October 2026, Cairo

Owner requested a cleaner GitHub and replacement of the continuous review loop with milestone handoffs.
Active planning PR: #72. Coordination index: #13. Automation remains PAUSED until explicit owner ready.

| PR | Disposition | Preserved work / reactivation condition |
|---|---|---|
| #8 | Closed as superseded planning | Historical strategy/risk proposal retained in its PR and branch; current owner contract and #72 control sequencing. |
| #23 | Closed as historical reference | Audit and evidence remain available in its PR/branch; no acceptance or implementation completeness implied. |
| #68 | Closed as superseded evaluation attempt | First STRAT-01 attempt used earlier rules; #69 and merged #70 contain the replacement rules/evidence. Retain original branch and ledger artifacts for provenance. |
| #50 | Closed as deferred, not accepted | Signal ingress/scorecard contracts remain on codex/relay-contract-v1 at 8066949e60ae506181394e0c66eb64906768e204. Reopen/rebase only when their native-Binance dependency enters an approved milestone. |
| #54 | Closed as deferred, not accepted | Post-smoke fixes remain on nc-post-smoke at eba337eb55a614f85ea93c6c321949f3eeb9e5f9. Triage required pieces into a runtime milestone; closure does not waive a defect or release gate. |

Issues #14 (feasibility) and #15 (copy sizing) remain open: their complete acceptance has not been established by
this cleanup. #13 remains the historical handoff index; do not erase comments used as acceptance evidence.

Remote branches t02-ui-baseline and t03-rollback-drill were verified as ancestors of master before removal.
Their commits remain in master history and local references. Other branches, including squash-merged branches,
backups and unique evidence, are retained unless equivalent content and recovery have been established.
No source, research dataset, ledger, audit evidence, credentials, release artifact or commit history is deleted.

The old PROJECT_STATUS snapshot is explicitly historical. Read owner contract/execution plan for the resumed
workflow. No need to keep all lanes busy: Claude implements one bounded milestone, Codex independently reviews
completion, and Cowork receives useful targeted assignments. Signed comments and one #13 pointer remain useful;
per-step approvals and repeated idle pings are superseded.
