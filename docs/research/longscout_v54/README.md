# LONGSCOUT-V54 results (research only): 1 of 2 pass

Frozen `3fc8b5e5` (rules) and `347775d8` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s54_double_top_4h | short | 112 | +0.153 | +0.128 | +0.158 | +0.162 | +0.127 | +0.183 | +0.093 | WIDE165_PASS | - |
| l54_double_bottom_4h | long | 104 | -0.103 | -0.124 | +0.001 | -0.257 | -0.144 | -0.084 | -0.173 | FAIL | mean R15, late, ex-best, R23, fresh subset, extra subset |

## s54_double_top_4h: pass, the most even lead so far (still a lead only)

- All 40 orders are positive (worst +0.088, median +0.153). Order 0: 111 closes (58 early / 53 late) on 70 coins,
  +19.18 R15, max closed drawdown 7.36 R15 (Standard). Exits: 49 time / 38 stop / 24 target.
- Subsets: fresh134 +0.183, extra31 +0.093.
- Concentration: ex-top-3 coins +0.087, ex-top-5 +0.051. Top coin AAVE contributes +3.85 R.
- By entry year (median R15 sum over orders): 2023 +4.64, 2024 +4.81, 2025 +4.56, 2026 +4.11. No signals before 2023
  in order 0 (the pattern needs 41 consecutive complete days plus the regime warm-up).
- Caveats: trade count is thin (median 112, just over 100); the mirror long rule (l54 double bottom) fails clearly
  (-0.103), so the edge is one-sided; 8 passes in 201 trials, so chance cannot be excluded.

Research only: no PAPER, no activation, no tuning. Next step is Codex reproduction and a public-data forward book.

Timing note (from the Codex V31-V51 review): "first 4h bar of the next day" means the signal sits on the 00:00 UTC bar
and the modeled entry fills at 04:00 UTC, after that bar closes. Forward books must keep that clock.

Mutation check: 9 of 9 planted mutants caught (two needed a widened 30-seed reference comparison and a synthetic
shallow-pattern test, both added before freeze).

Ledger after this round: 201 frozen, 201 evaluated, 8 passes.
