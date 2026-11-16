# LONGSCOUT trial-count correction (V74–V80)

Codex's V61–V76 review (PR #347 comment 6901683042) found that the running trial totals did not add up. Codex is right.

**The correct count:** V1–V60 had 217 trials. V61–V80 added 44 more:

| rounds | trials |
|---|---|
| V61 | 3 |
| V62–V65 | 2 each |
| V66–V68 | 3 each |
| V69–V80 | 2 each |

That makes **261 trials after V80**, with 11 screen passes. The pass list is unchanged.

**Where the errors were.** The pass/fail verdicts are not affected; only the reported totals were wrong.
- The V71–V74 PR comment said 251 after V74. The correct figure is 249. The V74 cases JSON correctly says 249.
- The V75 PR comment said 253. The correct figure is 251, and the V75 cases JSON correctly says 251.
- The V76 freeze passed 255 instead of 253. The frozen V76–V80 cases JSONs therefore overstate the total by 2:

  | round | stated | correct |
  |---|---|---|
  | V76 | 255 | 253 |
  | V77 | 257 | 255 |
  | V78 | 259 | 257 |
  | V79 | 261 | 259 |
  | V80 | 263 | 261 |

The frozen files are left unchanged, so the freeze history stays intact. This note is the correction.
