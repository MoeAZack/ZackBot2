# LONGSCOUT-V48 results (research only): 0 of 3 pass

Frozen `8c5f37af` (rules) and `c4dcdb90` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l48_btc_crash_day_buy_4h | long | 25 | +0.004 | -0.009 | +0.046 | -0.130 | -0.086 | -0.014 | +0.038 | FAIL | n<100, late, ex-best, R23, order 0 30-per-half, fresh subset |
| l48_btc_high_coin_lag_4h | long | 88 | -0.088 | -0.113 | -0.092 | -0.106 | -0.155 | -0.088 | -0.129 | FAIL | n<100, mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| s48_btc_rally_day_fade_4h | short | 40 | -0.145 | -0.164 | -0.051 | -0.259 | -0.228 | -0.133 | n/a | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |

Note on `n/a` in the extra31 column: in at least one of the 40 orders the max1 slot never closed a trade on an extra31 coin, so the frozen subset gate returns no value and counts it as a fail. This is the gate working as written; nothing was relaxed. Ledger after this round: 184 frozen, 184 evaluated, 6 passes.
