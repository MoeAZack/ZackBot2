# LONGSCOUT-V68 results (research only): 0 of 3 pass

Frozen `dd3e9b77` (rules) and `7eb4d261` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s68_dormant_low_4h | short | 25 | +0.495 | +0.472 | +0.280 | +0.723 | +0.416 | +0.363 | +1.135 | FAIL | n<100, order 0 30-per-half |
| l68_dormant_high_4h | long | 33 | +0.000 | -0.023 | +0.260 | -0.433 | -0.061 | +0.253 | -0.303 | FAIL | n<100, late, ex-best, R23, order 0 30-per-half, extra subset |
| l68_breakout_retest_4h | long | 184 | -0.071 | -0.093 | -0.085 | -0.042 | -0.095 | -0.066 | -0.085 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Notes: s68_dormant_low is positive in every slice (early, late, ex-best, both subsets) but has only 25 median closes, so it fails the
n>=100 and 30-per-half gates; it joins the thin near-miss list (s55, l62, s63, l67) and is not a pass. No gate was relaxed.
Tests: prefix, independent reference loops, synthetic retest/dormant edges, regime gate and gap tests; 15 planted mutants all caught.
