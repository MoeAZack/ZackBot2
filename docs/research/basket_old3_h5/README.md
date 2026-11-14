# BASKET-OLD3-H5 result: NOT_A_LEAD (descriptive, consumedDEV, no activation)

Contract: Codex GO PR #337 6832559586 / #219 6832557765. Source frozen at 347c4b71. Trial record (data/rules hashes verified before outcomes) at 85e12a28.
Data: cohort9_1h manifest c11ed256 (all 9 file hashes match), rules d1ef2a9c. Window: first processed bar 2024-11-17 12:00 UTC (4h index 220, 1h index 883), end 2026-09-28 00:00 UTC exclusive.
One trial: 24 native tapes (4 sleeves x 3 costs x 2 paths), then 720 replays (3 books x 3 costs x 2 paths x 40 orders). All outcomes retained in `results/`.

## Net return, % of $4,000 (median over 40 orders [p10, p90]); fixed $8 risk, $3,333.33 cap
| Book | 7bp path | 7bp worst | 15bp path | 15bp worst | 23bp path | 23bp worst |
|---|---|---|---|---|---|---|
| OLD3 alone | +7.4 [+5.0, +9.7] | -2.8 | +4.0 [+1.6, +6.3] | -6.7 [-8.6, -4.3] | +0.6 | -10.5 |
| H5 alone | +7.2 | +7.2 | +4.6 [+3.3, +5.8] | +4.6 | +1.9 [+0.6, +3.3] | +1.9 |
| Combined | +14.5 | +4.2 | +8.4 [+6.1, +11.1] | -2.3 [-4.4, +0.5] | +2.2 | -8.8 |

Median marked DD at 15bp: OLD3 9.0% path / 15.6% worst; H5 2.5%; combined 9.1% / 15.4%. Entries/week: OLD3 ~4.3-4.9, H5 ~2.1, combined ~6.3-7.0.

## Label conditions (all required)
- 15bp combined > OLD3: 40/40 path, 40/40 worst. Combined > H5: 40/40 path, **0/40 worst**.
- Combined 15bp positive before and after 2025-10-01 in every order: **fails** both paths.
- Combined 23bp positive in every order: **fails** both paths.
=> **NOT_A_LEAD.**

## What the numbers say
- **No interaction.** The 2.5% open-risk cap and the Cairo 3% halt never bound (seed 0, 15bp: every refusal is a sleeve cap). So combined = OLD3 + H5, and "combined vs OLD3" equals H5's own contribution.
  - Average open risk is $14 (peak $47) of the $100 allowed. Peak gross notional is about $2.1-2.3k (0.6x).
- **H5 is the stable part:** positive at every cost and both paths, low DD, about 2 entries a week. Its seed-0 later period (after 2025-10-01) is +$99 vs +$52 before.
  - Top coin is 73% of its P&L (seed 0, 15bp).
- **OLD3 depends on the candle path.**
  - Positive on the normal path, negative on worst at every cost.
  - HOT (trail + tp1) is the main loser on worst: -$305 seed 0, 15bp. On the normal path it is -$49.
  - BEAR flips from +$131 to -$49. EMA (no intrabar stop moves) is identical in both paths at +$106.
- **Phantom influence is large.**
  - Seed 0, 15bp combined: 958 refused candidates, about 44k phantom hours.
  - All come from the max-2 / max-1 sleeve caps against tapes built with one lot per coin.
  - Per Codex point 6, any positive reading would need an admission-aware reproduction first. It does not change this NOT_A_LEAD label.

## Identities and checks
- Native identity (fills cash minus native funding equals `backtest.run` trade P&L, 1e-8) held for every closed lot in all 24 tapes.
- The 1h target funding equals native funding for H5 exactly. For the 4h sleeves it is reconciled per tape in `results/tapes.csv`.
- No exchange-floor skips occurred. HOT tp1 partials ran at the actual $8 quantity: 64 lots on the normal path, 57 on worst.
- Not done: the measured comparison of canonical 4h bars against the PR #339 15m-derived bars.
