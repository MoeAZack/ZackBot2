# LONGSCOUT-V51 results (research only): 1 of 3 pass

Frozen `d7ad8167` (rules) and `f6243ac7` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s51_momentum_accel_4h | short | 219.5 | +0.063 | +0.037 | +0.106 | +0.019 | +0.037 | +0.042 | +0.081 | WIDE165_PASS | - |
| l51_momentum_accel_4h | long | 236 | -0.085 | -0.107 | -0.080 | -0.096 | -0.120 | -0.106 | -0.042 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| r51_btc_quiet_squeeze_4h | both | 25 | -0.243 | -0.272 | -0.127 | -0.352 | -0.353 | -0.202 | n/a | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |

## s51_momentum_accel_4h: thin pass, a lead only

- All 40 orders are positive (worst +0.006, median +0.063). Order 0: 223 closes (116 early / 107 late) on 88 coins,
  +9.15 R15, max closed drawdown 17.07 R15 (Elevated). Exits: 93 time / 91 stop / 39 target.
- Subsets: fresh134 +0.042, extra31 +0.081, so both are positive, unlike l40.
- Concentration: ex-top-3 coins +0.000, ex-top-5 -0.033. The edge depends on a handful of coins (1INCH, APE, 1000SHIB).
- By entry year (median R15 sum over orders): 2022 +2.92 (7 trades), 2023 -0.15, 2024 +8.55, 2025 +1.35, 2026 +1.01.
  Most of the total comes from 2024.

With 7 screen passes in 193 trials, chance is a likely explanation. Research only: no PAPER, no activation. Next step
would be Codex reproduction and a forward test, not tuning.

Mutation check: 8 of 9 planted mutants caught. Removing `_consec(D, 2)` from the squeeze rule is behaviour-equivalent,
because a missing day already makes the joined BTC return NaN, so the 7-day deviation and the flag are false.

Ledger after this round: 193 frozen, 193 evaluated, 7 passes.
