# PROT-CMP-01: which profit protection to run before mainnet

Research only. No engine, settings, runtime or account change. Base: master `42ef787f`.

## Question
Winners regularly turn into losses. In the cohort's own 4h strategies, about 1 in 10 trades that reach **+1R** in favour still
closes at a **real loss** (worse than -0.25R). Which stop-moving rule stops that, without costing more profit than it saves,
after fees, slippage and funding?

## Method
- **Strategies:** the 13 enabled 4h cohort slots (settings `SLEEVES`, read 16 Nov 2026), each run alone. Each uses risk 2%,
  max 4 positions, and its own sides and cohort overrides.
- **Variants (14):** `BASE` is the cohort as it runs. `NONE` removes every stop mover. Eight `prot` settings combine
  activation at 0.75 / 1 / 1.5 / 2R with a trail of the strategy's own / 2 / 3 / 4 ATR / none. There are also two `runner`
  lock-step settings. The `prot` variants change only the stop movers; entries, targets, tp1 partials and time exits stay.
  The runner variants are a different exit regime: `backtest.run` also drops `tp_r` / `max_bars` for them, so they are
  shown for reference only.
- **Costs:** 7, 15 and 23 bp all-in per executed side (FWD-01's levels). Funding is the backtest's `FUND_PER_BAR` = 0.00005 per 4h bar
  (0.5 bp per bar, about 1 bp / 8h). It is charged as a cost on every open position, longs and shorts alike, on the bar's
  close. (Corrected 16 Nov: an earlier version of this report said 3 bp / 8h; the runs always used 0.5 bp per bar.)
  **Sensitivity:** with funding at 3 bp / 8h (0.00015 per bar) the recommendation is unchanged. ΔR at 15 bp, summed over
  the 8 strategies, is A +70, B +68, L +21, against +73 / +69 / +20 at the default.
  `prot`'s breakeven buffer is pinned to the live engine's own (about 12 bp: `prot.PROT_FEE` / `PROT_SLIP`). The research
  therefore tests a slot exactly as `engine.py` would run it, even when real costs are higher.
- **Open trades:** trades still open at a period's end are not in ΔR. Their unrealised P&L is reported as `open_usd`, and it
  was in the recommended setting's favour in 8 of 9 path tests.
- **Periods (fixed before looking):**
  - **A:** the 40-coin data, first half (Oct 2024 to Oct 2025). This is where choices were made.
  - **B:** the second half (Oct 2025 to Oct 2026). Confirmation only.
  - **L:** 8 coins, Jan 2022 to Sep 2024, which includes the 2022 bear market. Confirmation only.
- **Intrabar path check:** a breakeven stop raised inside a 4h candle depends on whether the high or the low came first,
  which a 4h bar does not record. `prot_path_check.py` replays every 4h candle of the 8 coins (Aug 2022 to Oct 2026,
  71,977 candles, 15 unresolved) along its four real 1h candles. It runs in two modes: `h1` (the real order) and
  `h1worst` (the real order, each hour worst-case for the position: a conservative bound). The backtest's own 4h
  worst case (`worst4h`) was also run; it is a very harsh bound that assumes every candle hits its favourable extreme first.

## Result
**Recommendation: fee-adjusted breakeven at +1R, then the strategy's own trail. Apply it on 8 strategies only.**
- **Setting:** `prot = {at_r: 1}`, plus `trail_atr: <own>` where the strategy trails. Set `be_r = 0` and `trail_atr = 0` so
  `prot` is the only stop mover. Cohort tp1 / max_bars overrides stay as they are. The exact dicts come from
  `prot_compare.recommended_mgmt()`.
- **On:** bear_breakdown, ema_st, exp_breakout, exp_mom_short, exp_range, exp_trend, hot_coin, pullback_rsi.
- **Unchanged:**
  - donchian_ens, ema_mom, rotation and squeeze_tp did worse on period A, and the exclusion was chosen on A only.
  - breakout_pyramid was better on A and B but -76R on the 1h path check (15 bp). That is mixed, so it waits for PYR-01.

ΔR is the change in total R against `BASE`, summed over the 8 strategies. A red loss is a trade that reached +1R and
closed below -0.25R.

| Test | 7 bp ΔR | 15 bp ΔR | 23 bp ΔR | Red losses, BASE → rec (15 bp) |
|---|---|---|---|---|
| 4h path, period A (pick) | +58 | +73 | +59 | 33 → 2 |
| 4h path, period B | +50 | +69 | +79 | 36 → 1 |
| 4h path, period L (2022-24) | +14 | +20 | +20 | 70 → 10 |
| real 1h path, 2022-26 | +108 | +104 | +117 | 117 → 11 |
| 1h worst-case bound | +60 | +57 | +60 | 119 → 9 |
| 4h worst-case bound, A / B / L | +64 / +106 / -18 | +27 / +64 / -20 | +23 / +12 / -24 | 33/38/77 → 1/0/3 |

- **Red losses fall by about 90%** in every period, at every cost level and under every path model.
- **Total R does not get worse** in any test except the harshest 4h worst-case bound. The median max drawdown is
  unchanged (within ±1 pp).
- **The gain is concentrated:** ema_st gives +81R of the +104R on the real 1h path. The other 7 strategies sum to about
  +22R. Some of the 8 are slightly worse in one test:
  - exp_breakout: -13R on the 1h path and -5R in L;
  - hot_coin: -10R in B;
  - pullback_rsi: -2R in B.

### What does not work
- **A tight trail on everything** (`prot` with a 2 or 3 ATR trail, which is what PR #333's tests used) costs the trend
  strategies their fat tails. On the real 1h path at 15 bp, across all 13 strategies, it lost 187R (3 ATR) and 369R (2 ATR)
  against BASE. It lost most on rotation (-128 to
  -165R), breakout_pyramid (-76 to -115R) and ema_mom (-51 to -64R).
- **The runner lock-step** looked best on A and B, but it is negative on L and on the real 1h path.
- **Activation at 1.5 or 2R** leaves the +1R-to-1.5R winners unprotected, so red losses do not fall.

### Account 3 (read-only finding)
Account 3 runs `prot {at_r 1, trail_atr 2}` on exp_breakout, exp_trend and ema_st (short). On those three strategies, the
recommended setting (no trail, because none of the three has its own) beat the trail-2 setting in four of five tests:

| Test (15 bp) | Account 3 setting ΔR vs BASE | Recommended setting ΔR vs BASE |
|---|---|---|
| real 1h path | -29 | +70 |
| 1h worst-case bound | -101 | +61 |
| period A | +133 | +27 |
| period B | +24 | +36 |
| period L | -51 | -4 |

The account 3 setting won only in period A. Both settings cut red losses about equally. This test traded both sides of
ema_st; account 3 trades it short only. Changing account 3 is a runtime action and needs moe's written approval. It
should be made only when that book is flat.

## Limits
- **Protection does not create an edge.** At 15 to 23 bp most of these strategies lose money in BASE as well. This work
  only stops winners from becoming losers.
- **Live buffer is about 12 bp.** If real costs are nearer 15 to 23 bp, a stop at the live breakeven nets a small loss
  (about 0.1R or less) rather than zero. That is still counted as a scratch, not a red loss, and the totals above include it.
  A `prot.cost` matched to measured fills is a later calibration step.
- Each strategy runs alone. The account caps (2.5% open risk, 3% daily halt) and slot interactions are not modelled.
- MFE is measured on candle highs and lows from the entry fill to the exit candle. That is slightly generous on the exit
  candle, and equally so for every variant.
- **Selection caveat (Codex review):** the period A selection is not provably made before the other results existed. All
  periods were run in one job, so the record shows the rule (pick on A, confirm on B / L / 1h) but cannot prove the order.
  Treat B, L and the 1h check as supporting evidence, not as a sealed holdout. The forward books are the real test.
- The exclusion list for the 4 strategies was chosen on period A. The breakout_pyramid exclusion was chosen after seeing
  the 1h path check, and is stated as such.
- The 1h path check covers 8 coins only.

## Reproduce
```
python tools/research/prot_compare.py OUT                      # 1638 runs, about 1 min on 4 cores
python tools/research/prot_compare.py OUT --pessimistic worst --variants BASE,P1_own,RUN_lock
python tools/research/prot_path_check.py OUT --variants BASE,P1_own,P1_T3,RUN_lock,P1_T2
```
Rows and meta, with data sha256 and head, are in `research_evidence/runs/prot_cmp_01/{path4h,worst4h,h1}/`.
