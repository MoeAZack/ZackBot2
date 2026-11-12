# H5-WIDE-D01 result: unchanged H5 does not transfer to 31 new coins (2026-11-12)

Contract: Codex prereg `622635c7…` (evidence `5b768545`). Runner `research/h5wide_d01/run_d01.py` (sha256 `e415c8bb…`), frozen
at `c1de983a` before the data; data quality, input hashes and eligibility posted at `ec7db4cf` (#219 comment 6826892172)
before any economics. Window 2022-08-26..2026-10-04, split 2025-01-01 (consumedDEV). Fixed $8 risk (median actual
$7.98-7.99, max $8.00), max 1 position per arm, PIT liquidity, 0 sizing skips. Figures are costed (7/15/23 bp per executed
side incl. slippage, funding proxy 1 bp/8h); "gross" excludes fees and funding. Median [p10, p90] over 40 tie orders.

| Arm | closes/wk | gross R | R@7 | R@15 | R@23 | early R@15 | later R@15 | ex-best R@15 | closed-R DD@15 | orders R@15>0 |
|---|---|---|---|---|---|---|---|---|---|---|
| original9 (control) | 2.32 | +0.227 | +0.155 [+0.142,+0.176] | +0.091 [+0.078,+0.116] | +0.027 [+0.012,+0.054] | +0.134 | +0.019 [-0.007,+0.069] | +0.062 | 20R | 100% |
| extra31 | 3.18 | +0.009 | -0.032 [-0.051,-0.004] | -0.068 [-0.087,-0.041] | -0.104 | -0.121 | +0.004 | -0.086 | 75R | 0% |
| union40 | 3.35 | +0.091 | +0.043 | +0.000 [-0.023,+0.018] | -0.043 | -0.061 | +0.077 | -0.021 | 59R | 50% |

Contract decision (>=100 closes, >=30 each period, R@15 > 0 both periods, R@23 > 0, ex-best R@15 > 0, positive R@15 in
>= 80% of orders): **extra31 FAIL, union40 FAIL.** Frequency rises 37-45% over the matched 9, but the economics fail.
The original-9 control reproduces Codex's selection result (about 2.3 closes/week, +0.09R at 15 bp). At 23 bp its later
period is -0.052R and ex-best is -0.004R.

**Read.** On 31 coins it was not selected on, H5's gross edge is about zero (+0.009R). Its edge on the original 9 is
therefore most likely a selection effect, not a general volume-impulse effect. The earlier 11-coin pilot (compounding,
15 months) overstated transfer. Worst extra coins at 15 bp, order 0: BCH -14.9R, AAVE -14.3R, ENA -9.7R, MANA -8.8R.
Recommendation: do not widen H5. Treat the account-4 H5 canary as a low-confidence probe, not the anchor of a frequent
basket.
