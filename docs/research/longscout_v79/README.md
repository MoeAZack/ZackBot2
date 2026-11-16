# LONGSCOUT-V79 results (research only): 0 of 2 pass

Frozen `cc989607` (rules) and `dcdb9872` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s79_weekly_turn_down_4h | short | 53 | -0.138 | -0.163 | -0.155 | -0.109 | -0.193 | -0.058 | -0.384 | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |
| l79_weekly_turn_up_4h | long | 60 | -0.290 | -0.315 | -0.271 | -0.326 | -0.329 | -0.355 | -0.124 | FAIL | n<100, mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Notes: both weekly-turn rules lose clearly and are thin. Tests: prefix, independent reference loop (with two removed-day gaps),
synthetic weekly-run edges (equal highs/lows, run length, incomplete and missing weeks, mirror), regime gate with forced regimes; 11
planted mutants all caught (three after synthetic tests were added).
