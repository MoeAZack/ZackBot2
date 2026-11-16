# LONGSCOUT-V85 results (research only): 0 of 2 pass

Frozen `d6f276f3` (rules) and `2ae3914b` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s85_volume_confirms_selloff_4h | short | 136 | +0.085 | +0.059 | +0.018 | +0.148 | +0.044 | -0.006 | +0.318 | FAIL | fresh subset |
| l85_volume_confirms_rally_4h | long | 173 | -0.138 | -0.154 | -0.090 | -0.251 | -0.192 | -0.119 | -0.176 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Notes: l85 loses after costs. s85 passes every rule v1 gate (mean R15 +0.085, R23 +0.059, both halves and ex-best positive) but FAILS
the frozen subset gate: fresh134 median R15 -0.006 (extra31 +0.318). The edge sits in the 31 extra coins, so this is a thin near-miss
and stays FAIL; no parameter is changed and no rerun is made. Ledger: 271 trials after V85, 11 passes. Tests: prefix, independent
pure-Python Pearson reference loop over several seeds (with a removed-day gap), synthetic heavy-volume rally / selloff (exact first
cross day), negative correlation inside a 30-day gain gives no short, missing day j-31 / j-30, level and R boundaries (monkeypatched
correlation), R base day j-30, regime gate with forced regimes, hour-zero. 14 planted mutants, all caught (the R-shift mutants after
the base-day test was added).
