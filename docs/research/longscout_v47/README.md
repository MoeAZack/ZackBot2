# LONGSCOUT-V47 results (research only): 0 of 3 pass

Frozen `49c3a8f0` (rules) and `cd619b9b` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r47_us_session_hold_4h | both | 1405 | -0.051 | -0.080 | -0.051 | -0.053 | -0.054 | -0.049 | n/a | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l47_asia_session_hold_4h | long | 1382 | -0.054 | -0.082 | -0.054 | -0.061 | -0.060 | -0.060 | -0.042 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l47_strong_close_overnight_4h | long | 616 | -0.109 | -0.141 | -0.120 | -0.103 | -0.116 | -0.113 | -0.099 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Note on `n/a` in the extra31 column: in at least one of the 40 orders no extra31 trade closed, so the frozen subset gate returns no value and counts it as a fail (gate as written, nothing relaxed). The rule fails on every other gate anyway.
