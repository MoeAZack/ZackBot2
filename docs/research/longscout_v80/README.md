# LONGSCOUT-V80 results (research only): 0 of 2 pass

Frozen `28961fd5` (rules) and `de63cd62` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s80_triple_top_4h | short | 17 | +0.535 | +0.511 | +0.764 | +0.375 | +0.446 | +0.509 | +0.406 | FAIL | n<100, order 0 30-per-half |
| l80_triple_bottom_4h | long | 26 | -0.074 | -0.098 | -0.219 | +0.093 | -0.153 | -0.367 | +0.793 | FAIL | n<100, mean R15, early, ex-best, R23, order 0 30-per-half, fresh subset |

Notes: s80_triple_top makes +0.535 per trade and is positive in every slice, but it has only 17 median closes, so it fails n>=100 and
30-per-half. It is a thin near-miss of the same pattern family as the s54 double top, not a pass. No gate was relaxed.
Run note: the first V80 run hit a full scratch disk while copying data. The copied data folders were deleted and the same frozen cases
(rules 28961fd5, cases de63cd62, both pushed before any outcome) were re-run unchanged.
Tests: prefix, independent reference loop (with a removed-day gap), synthetic triple edges (spread, depth, close and previous-day
equality, all three window boundaries, mirror), regime gate with forced regimes and gap test. 14 planted mutants, all caught.
This is the final LONGSCOUT round (moe: "stop at 80").
