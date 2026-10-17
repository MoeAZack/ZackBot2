# failed_breakout_scalp: direction vs BTC regime (arm A none / arm B counter)

Spec: ZackBot2 #13 comment 6228353544. Research only, PAPER, non-promotable. This uses the development window only:
2024-10-14T00:00:00Z to 2026-09-28T00:00:00Z (end exclusive). Key `failed_breakout_scalp`, `--mgmt defaults`, 15m research
cache plus the 4h regime cache. Runner commit `753dc61` (clean tree).

- **Arm A (`--regime-refuse none`)** is the unchanged runner. Its packet is byte-identical to pre-change `b52dd5e`, apart
  from the runner and packet digests.
- **Arm B (`--regime-refuse counter`)** refuses a LONG entry signal when the fill-time regime is `btc_below_ema200`, and a
  SHORT entry signal when it is `btc_above_ema200`. The regime comes from `regime_at`: the last 4h candle that closed at or
  before the fill. When `regime_at` returns `n/a`, the signal is not refused. The refusal is applied before entry: the
  signal is masked in the book's cached raw signals before `backtest.run` reads them. A later signal can therefore still
  enter. Nothing else changes.
- **Views.** `acct` uses the app account defaults: it compounds and busts in both arms. `fixr` uses
  `--start 1e9 --risk 1e-6` (fixed risk) and is the primary view.

Files: `run_all.sh` (16 runs, CORE-10 first, 4 in parallel), `analyze.py` and `summary.json` (every metric per tier x side x
arm x view, the B-A deltas, the per-quarter net R, the gross/fee/slip/funding decomposition, the refused and substitute
counts, the baseline check and the decision). Packets and logs (`acct/`, `fixr/`, `logs/`) are not committed: the fixr
packets are larger than 1 MB.

## Headline (fixr view)

| tier | side | A n | A net R | A PF | B n | B net R | B PF | refused signals | substitutes | quarters B>A |
|---|---|---|---|---|---|---|---|---|---|---|
| CORE-10 | long | 10489 | -2649.2 | 0.589 | 5335 | -1412.8 | 0.574 | 5734 | 0 | 7/8 |
| CORE-10 | short | 10771 | -2667.7 | 0.600 | 4811 | -1111.3 | 0.620 | 6386 | 1 | 8/8 |
| CORE-20 | long | 16051 | -4161.0 | 0.586 | 8208 | -2215.6 | 0.574 | 11253 | 4 | 8/8 |
| CORE-20 | short | 17220 | -4153.2 | 0.613 | 7678 | -1732.0 | 0.630 | 12922 | 1 | 8/8 |

`substitutes` counts trades in B whose (symbol, entry) is not in A. "Refused signals" counts masked signal bars. Many of
these bars fell while a position was already open, so they would not have been entries in A either.

## Decision (the fixed rule, per side)

The rule is applied per side. B improves only if all three hold: net R is higher in B than in A on both tiers; PF_B >= PF_A
on both tiers; and quarterly B-A net R is > 0 in at least 2/3 of quarters on both tiers.

- **fixr / short: B improves.** Net R is higher on both tiers. PF goes 0.600 -> 0.620 on CORE-10 and 0.613 -> 0.630 on
  CORE-20. B-A is positive in 8/8 quarters on both tiers.
- **fixr / long: no change.** PF falls on both tiers (0.589 -> 0.574 and 0.586 -> 0.574).
- **acct, both sides: no change.** Every acct run busts in both arms, so these numbers are dominated by when the account dies.

**Caveat.** "B improves" on shorts is mostly a smaller-sample effect. B is essentially A's in-regime subset: it has very
few substitutes, and its trades split by regime as A's same-regime trades do. Most of B's higher net R comes from taking
fewer trades of a strategy that loses on every trade on average. The real per-trade gain is small: avg R goes
-0.248 -> -0.231 on CORE-10 and -0.241 -> -0.226 on CORE-20. B stays deeply negative (PF about 0.62). Fees plus slippage
are about 20x the gross. This is not a profitable configuration.

## Baseline check

Arm A matches `ZackBot2_sce/research_evidence/scalp4_core/summary.json` for failed_breakout_scalp exactly in all 8
tier/side/view cells: same n, net R and PF. That baseline ran at runner commit `a53a649`.
