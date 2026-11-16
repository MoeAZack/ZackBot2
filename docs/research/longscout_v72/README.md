# LONGSCOUT-V72 results (research only): 0 of 2 pass

Frozen `7eed7d15` (rules) and `fe6e78fd` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s72_distribution_4h | short | 203 | +0.040 | +0.016 | +0.045 | +0.031 | +0.014 | +0.054 | -0.005 | FAIL | extra subset |
| l72_follow_through_4h | long | 83 | +0.006 | -0.019 | -0.123 | +0.181 | -0.057 | -0.069 | +0.334 | FAIL | n<100, early, ex-best, R23, fresh subset |

Notes: s72_distribution passes every rule-v1 gate (+0.040, n 203, both halves, ex-best, R23) but its extra31 subset median is
-0.005, so the frozen subset gate fails it. It is a FAIL; the gate is not relaxed. Recorded as a near-miss.
Tests: prefix, independent reference loops (with a removed-day gap), synthetic follow-through (age, size, volume, first, tied low) and
distribution (count, threshold, equal volume, first day) edges, regime gate and gap tests. 15 planted mutants: 13 caught (two after
synthetic tests were added); two are behaviour-equivalent: including day j in the low window (a +3% day cannot be the minimum), and
dropping `dd > 0` (the count can only reach 6 on a day that is itself a distribution day).
