# FBL-BT01 (Critical, issue #13): backtester intrabar path. Review request (local branch `fbl-bt01-path`, base 874cfd8)

## The defect

`backtest.run` filled DCA safety orders when the candle's low reached them, recomputed the basket TP from the new
average, and then tested that TP against the **same candle's high**. On a red candle (path open -> high -> low -> close
for a long) the high came before the fill, so the fill-then-TP sequence the backtester booked cannot happen. Pyramid
adds, tp1, the ladder, breakeven and tp_r were all tested against the same favourable extreme in a fixed code order, not
in the order the market reaches them.

## The fix: one declared path, every intrabar event in path order

`backtest.path_points(o, h, l, c, side, worst=False)` is the only path policy:

| Candle | Path |
|---|---|
| green (c > o) | open -> low -> high -> close |
| red (c < o) | open -> high -> low -> close |
| doji (c == o), or every candle with `pessimistic='worst'` | worst case **for this position's side**: favourable extreme first, adverse last (long o->h->l->c, short o->l->h->c) |

`walk_path` (inside `run`) walks the open point and the three legs of that path for each open position:

- **Adverse leg** (price moves against the position): DCA safety orders and any stop raised earlier in this candle, in
  the order price meets them. Ties go to the stop (conservative). A raised stop that is already behind the leg start is
  filled at the leg start (the engine closes at the mark, `stop_crossed`).
- **Favourable leg**: basket TP, pyramid add, tp1, the next ladder level, tp_r, in **price order** (ties: that order).
- **End of every leg** (also a zero-length one, e.g. a candle that opens at its high; not the bare open point): `best`
  is updated and the stop ratchets (be_r trigger, chandelier trail, trailing TP, runner) run. A stop raised here can
  only be hit by a **later** leg.
- A level set or recomputed at a point of the path (basket TP after a fill, the next pyramid level, a raised stop) is
  therefore reachable only by later price moves.

Unchanged conventions (kept on purpose, as they were conservative or not intrabar):
- ~~The stop as it stood at the open is checked first against the whole candle~~ - changed in Codex round 1 (see that
  section): only a gap through the stop at the open is handled before the walk; the stop is an adverse-leg event, and
  stop-first survives only as "a stop inside the candle beats any target in that candle" (adds still fire first).
- Entries at the next open; trailing entries keep their own `c >= o` path rule (they fill mid-candle and are managed
  from the next candle, `skip_i`); signal / time exits at the close; the liquidation check at the adverse extremes.
- A safety order or add refused by a gate (breaker, leverage cap, risk rules, halt) is not retried in the same candle.

`replay.py` (the engine-vs-backtest harness) walks dojis with the same policy, taking the side held on that coin (no lot,
or both sides held: the green order as before). Non-doji candles are walked exactly as before.

### Behaviour changes beyond the DCA TP (for review)

1. **Doji = worst case per side.** Measured impact on the presets: none (`active_dca` 1h and `boost` 4h give identical
   results with dojis walked as green instead). The old code treated a doji as "adverse extreme not after" for both sides
   (a stop raised at the favourable extreme was only tested against the close). Measured on Active's 1h breakout slot
   run alone: 1 of 2,073 trades changes (stopped on a doji one candle earlier).

Kept as before (deliberately; see "For Codex to decide" 3): a trailing stop that moves only because of the fresh ATR of
the candle that just closed takes effect after the first leg of the next candle, not at its open. Measured alternative
(ratchet at the bare open, which is what the live engine does - `cycle()` refreshes `atr_now` at the close and the
first `manage()` tick raises the stop): strict replay 2 (no DCA) matched 300/301 -> 301/301 but its return gap went
2.17 -> 3.16 pp (strict limit 3.0); 16 of 2,073 1h breakout trades exit earlier. Not adopted in this ticket.
2. **be_r trigger from the best price so far** (engine semantics: `lot['best']`), not only this candle's favourable
   extreme. It matters only when the average moved after the trigger (pyramid adds).
3. **tp1 with `tp1_frac` = 1** now ends the position (the old code left a zero-quantity position that later booked a
   duplicate trade).
4. `pessimistic='worst'` now orders every event on the worst-case path (it used to apply only to raised stops).

## Tests

`tests/test_bt_intrabar_path.py` (14 tests, ~12 s; hand-built candles, signals and ATR injected, numbers checkable on
paper):
- `test_path_points_policy`: the declared table above.
- `test_red_candle_dca_long_cannot_hit_the_recomputed_basket_tp`: **the reproducer**. Old code books `tp` on the fill
  candle; new code keeps the basket open and takes the TP on a later candle that really reaches it.
- `test_green_candle_dca_long_does_hit_the_basket_tp_after_the_fill`: low first, then the high reaches the new TP; exact
  P&L checked (fees, slippage, funding).
- `test_doji_dca_long_is_treated_as_the_worst_path`.
- `test_short_side_symmetry`: short DCA on green (no TP), red (TP) and doji (no TP) candles.
- `test_stop_before_target_when_both_existed_at_the_open`: unchanged stop-first convention, red and green.
- `test_pyramid_add_fires_in_price_order_with_tp1`: pyramid analogue. tp1 at +1R comes before the add at +1.5R on the
  way up; the old code added first and banked half of the enlarged size at the lower tp1 price. Exact P&L checked.
- `test_pyramid_red_candle_add_then_raised_stop_hit_by_the_later_low` (+ the green counterpart: not hit).
- `test_dca_runner_breakeven_raised_at_the_tp_is_hit_only_by_a_later_low` (green: not hit; red: hit in that candle).
- `test_trail_from_the_fresh_atr_ratchets_after_a_zero_length_first_leg`: a red candle opening at its high still gets
  the fresh-ATR trail after its (empty) first leg and is stopped by the low (exact P&L); a green candle dipping first is
  not stopped by a trail that only takes effect after that dip.
- **Engine vs backtester DCA replay** (`replay.run_replay`, the live `engine.Engine` against the simulated exchange,
  marks walked along the declared path in 8 steps per leg; `strategies.signals` patched to a single deterministic entry
  so both sides trade the same basket):
  - `test_engine_and_backtest_agree_on_dca_fills_and_tp_along_the_path[long, short]`: fill candle against the path
    (favourable extreme first): neither the engine nor the backtester takes the TP there; both take `basket_tp` two
    candles later; same entry candle, same exit candle, 1 safety order, |dR| <= 0.1, 0 position mismatches.
  - `test_engine_and_backtest_agree_when_the_path_allows_the_tp_in_the_fill_candle[long, short]`: fill first, TP after:
    both take it on the fill candle.

Old backtester (874cfd8 `backtest.py`) against the new test file: 6 fail (red reproducer, doji, short symmetry, pyramid
order, both engine-vs-backtester "against the path" cases); 8 pass (the unchanged behaviours).

### Mutation check (each mutant of `backtest.py` must fail the new test file): 9/9 killed

| Mutant | Failing tests |
|---|---|
| M1 basket TP tested against the candle extreme (the old same-candle test) | red reproducer, doji, short symmetry, DCA runner, engine-vs-bt [long, short] |
| M2 no path (every candle walked as green) | policy, red reproducer, doji, engine-vs-bt [long], engine-vs-bt allowed [short] |
| M3 doji walked as green (`c >= o`) | policy, doji |
| M4 favourable events by fixed priority, not price order | pyramid/tp1 order |
| M5 pyramid add tested against the candle extreme | pyramid/tp1 order |
| M6 raised stop never checked inside the candle | pyramid red-candle trail, DCA runner breakeven |
| M7 ratchet applied before the leg (stop hit by the leg that raised it) | pyramid red-candle trail |
| M8 zero-length leg skipped entirely (no ratchet after it) | zero-length-leg trail |
| M9 ratchet at the bare open as well | zero-length-leg trail (green dip case) |

## Causality / parity (tests/test_causality.py)

All 13 cases pass on the final code (per-case driver, 2 parallel): 6 x `test_engine_decisions_never_depend_on_unseen_prices`, 6 x
`test_backtest_matches_causal_engine_trade_by_trade`, `test_parity_catches_same_candle_information`.

Parity metrics, old -> new backtester on the same synthetic markets (engine side identical; no synthetic candle is a doji):

| Mode | engine / bt / matched trades | median / p95 abs(dR) | return gap pp | DD gap pp |
|---|---|---|---|---|
| trend long + pyramid | 4 / 4 / 4 -> same | 0.005 / 0.096 -> same | 0.15 -> 0.15 | 0.02 -> 0.02 |
| trailing both sides | 11 / 11 / 11 -> same | 0.031 / 0.073 -> same | 1.08 -> 1.08 | 0.25 -> 0.25 |
| breakout pyramid + trail | 11 / 11 / 11 -> same | 0.014 / 0.070 -> same | 0.42 -> 0.42 | 0.38 -> 0.38 |
| DCA basket | 3 / 2 / 2 -> same | 0.033 / 0.056 -> same | 0.18 -> 0.18 | 0.12 -> 0.12 |
| partial TP + breakeven | 9 / 9 / 9 -> same | 0.019 / 0.069 -> same | 0.14 -> 0.14 | 0.07 -> 0.07 |
| runner ladder | 2 / 2 / 2 -> same | 0.030 / 0.052 -> same | 0.00 -> 0.00 | 0.00 -> 0.00 |
| leak detector (spiky) | 13 / 13 / 13 -> same | 0.029 / 0.085 -> same | 0.52 -> 0.52 | 0.34 -> 0.34 |

Every parity metric is unchanged: the synthetic markets have no doji and no DCA fill followed by a TP against the path
(the DCA mode's one unmatched engine trade is the same before and after; within the allowed one flip). That is why the
deterministic engine-vs-backtester DCA test above was added. (An intermediate version that also ratcheted at the bare
open changed "trailing both sides" to return gap 1.18 / DD gap 0.06; not adopted, see "For Codex to decide" 3.)

## Strict replays (verify.py REPLAYS, 24 steps, ZB_REPLAY_STRICT=1)

Run in full (`test_engine_sim.py`, 24 steps per leg, strict gate on), on 874cfd8 and on this branch:

| Replay | 874cfd8 (old) | this branch | Strict gate |
|---|---|---|---|
| 1: MOM pyramid + DCA + Squeeze (last 900 4h candles) | engine +54.9 % / bt +52.1 %, matched 133/133, median 0.017, p95 0.118, return gap 2.81 pp, DD gap 0.03 pp | identical (every number) | PASS -> PASS |
| 2: breakout + bear shorts + Donchian (from bar 3000) | engine -8.9 % / bt -11.1 %, matched 300/301, median 0.019, p95 0.096, return gap 2.17 pp, DD gap 0.97 pp | identical (every number) | PASS -> PASS |

No tolerance was changed. Replay 1's 28 DCA trades contain no fill-then-TP-against-the-path candle in that window
(its 133 backtest trades are identical old vs new, checked trade by trade), so the strict gates did not move. An
intermediate version that also ratcheted stops at the bare open (engine-like ATR timing) made replay 2 match 301/301
but its return gap 3.16 pp failed the strict 3.0 pp gate; that version was not kept (see "For Codex to decide" 3).

## Presets: old vs new backtester, same data

> **Round 0 numbers (before Codex round 1).** Superseded: the full rerun with the round-1 code is in progress - see
> "Codex round 1 -> Presets (rerun after the round-1 fix)". Kept below for reference only.

Data: `data_long/` (core 8; 4h from 2021-12-19, 1h from 2022-08-26, to 2026-10-04), the jobs of `research_long.py` /
`research_long2.py` (`research_long.sleeves(preset, tf)`, $500, 10x cap, warmup 220, funding per bar scaled to the
timeframe). The 4h+1h profiles are the 50/50 side-by-side mix (`research_long.mixed`, daily curves, from 2022-09-04),
as their quoted numbers were made. The **old** column reproduces the quoted research numbers exactly (Active DCA
$4,637 / PF 1.75 / -25.9 %, Steady mix $4,015 / -14.7 %, Boost + Active $8,112 / -32.5 %, Active $3,947 / -49.9 %).
Each run was one command (3-10 s). The profiles without a DCA slot are unaffected (`original`: $5,188 / -52.7 % old and new).

Each cell is old -> new. Per-year returns are calendar years of the curve (first and last year partial).

| Preset | TF | Window | Final equity old -> new | PF old -> new | Max DD old -> new | Trades old -> new | Win % old -> new | 2022 old -> new | 2023 old -> new | 2024 old -> new | 2025 old -> new | 2026 old -> new |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| calm | 4h | 2022-01-25 to 2026-10-04 | $1,169 -> $1,158 | 2.21 -> 2.18 | -13.3% -> -13.3% | 595 -> 593 | 44.7 -> 44.2 | -7.1% -> -7.1% | 36.1% -> 36.2% | 31.6% -> 31.0% | 12.1% -> 12.0% | 25.3% -> 24.8% |
| balanced | 4h | 2022-01-25 to 2026-10-04 | $3,593 -> $3,222 | 2.34 -> 2.18 | -33.6% -> -33.6% | 864 -> 826 | 55.8 -> 52.4 | -19.7% -> -19.7% | 91.4% -> 90.5% | 104.5% -> 98.7% | 33.0% -> 28.3% | 71.8% -> 65.2% |
| aggressive | 4h | 2022-01-25 to 2026-10-04 | $6,916 -> $5,356 | 2.29 -> 2.05 | -45.2% -> -46.0% | 976 -> 927 | 60.2 -> 56.2 | -27.4% -> -28.4% | 128.9% -> 125.1% | 167.2% -> 148.3% | 51.5% -> 38.0% | 105.7% -> 94.1% |
| boost | 4h | 2022-01-25 to 2026-10-04 | $9,274 -> $5,043 | 2.65 -> 2.01 | -41.0% -> -41.3% | 821 -> 770 | 77.3 -> 73.6 | -26.6% -> -27.2% | 226.3% -> 213.9% | 95.3% -> 56.4% | 59.7% -> 27.4% | 148.4% -> 121.4% |
| active | 1h | 2022-09-04 to 2026-10-04 | $3,947 -> $1,424 | 1.18 -> 1.09 | -49.9% -> -60.3% | 4416 -> 4054 | 60.6 -> 55.6 | -24.6% -> -33.6% | 111.2% -> 58.5% | 88.8% -> 46.6% | 46.1% -> 23.3% | 79.7% -> 49.7% |
| active_dca | 1h | 2022-09-04 to 2026-10-04 | $4,637 -> $450 | 1.75 -> 0.97 | -25.9% -> -46.2% | 2491 -> 2223 | 94.1 -> 90.0 | 21.7% -> -9.7% | 133.1% -> 20.9% | 94.0% -> 4.1% | 49.9% -> 2.2% | 12.4% -> -22.6% |
| steady_mix | 4h half | 2022-01-25 to 2026-10-04 | $3,445 -> $3,019 | 2.57 -> 2.27 | -22.1% -> -23.0% | 773 -> 737 | 66.0 -> 62.4 | 1.0% -> 1.1% | 94.1% -> 92.3% | 109.9% -> 101.8% | 39.9% -> 33.8% | 19.7% -> 15.0% |
| steady_mix | 1h half | 2022-09-04 to 2026-10-04 | $4,637 -> $450 | 1.75 -> 0.97 | -25.9% -> -46.2% | 2491 -> 2223 | 94.1 -> 90.0 | 21.7% -> -9.7% | 133.1% -> 20.9% | 94.0% -> 4.1% | 49.9% -> 2.2% | 12.4% -> -22.6% |
| steady_mix | 4h+1h (50/50 mixed) | 2022-09-04 to 2026-10-04 | $4,015 -> $1,711 | 1.94 -> 1.6 | -14.7% -> -20.1% | 3264 -> 2960 | 87.5 -> 83.1 | 10.6% -> -5.1% | 115.5% -> 58.4% | 100.4% -> 66.3% | 45.6% -> 26.6% | 15.4% -> 8.1% |
| boost_active | 4h half | 2022-01-25 to 2026-10-04 | $9,274 -> $5,043 | 2.65 -> 2.01 | -41.0% -> -41.3% | 821 -> 770 | 77.3 -> 73.6 | -26.6% -> -27.2% | 226.3% -> 213.9% | 95.3% -> 56.4% | 59.7% -> 27.4% | 148.4% -> 121.4% |
| boost_active | 1h half | 2022-09-04 to 2026-10-04 | $3,947 -> $1,424 | 1.18 -> 1.09 | -49.9% -> -60.3% | 4416 -> 4054 | 60.6 -> 55.6 | -24.6% -> -33.6% | 111.2% -> 58.5% | 88.8% -> 46.6% | 46.1% -> 23.3% | 79.7% -> 49.7% |
| boost_active | 4h+1h (50/50 mixed) | 2022-09-04 to 2026-10-04 | $8,112 -> $4,049 | 1.51 -> 1.38 | -32.5% -> -40.0% | 5237 -> 4824 | 63.2 -> 58.5 | -13.8% -> -18.7% | 176.2% -> 150.7% | 93.1% -> 53.9% | 55.3% -> 26.4% | 127.3% -> 104.3% |

Where the difference comes from:
- **The DCA slot.** Example `balanced`: DCA sleeve P&L $174 -> $1 (373 -> 335 trades); `boost`: $1,724 -> $183;
  `active`: $1,186 -> $18. The 1h DCA slot alone (`active_dca`) goes from PF 1.75 to 0.97 and ends below its start.
- **The other slots** of the same profiles (ema_mom / ema_st, 4h): same trades, same exit candle, same reason, same R
  (491/491 in `balanced`, 221/221 in `boost`, 338/338 in `steady_mix` 4h half). Their dollar P&L is lower only because the
  shared equity they size from is smaller.
- The 1h breakout slot (`active`): 2,118 of 2,120 trades identical (exit candle, reason, R); the 2 others: one stopped on
  a doji a candle earlier (doji policy, +0.0007 R), one same exit with R -0.559 -> -0.546 (the leverage cap binds
  on the smaller shared equity).

## Presets labelled "unverified" (label only)

- `engine.UNVERIFIED_BT01 = 'unverified: backtest path fix pending re-validation (FBL-BT01)'`, set as
  `PRESETS[k]['bt']['unverified']` on every profile with a `dca_dip` slot: calm, balanced, aggressive, active,
  boost_active, steady_mix, active_dca, boost. Names, notes, numbers, sleeves and the default profile are unchanged
  (the notes are byte-identical; the label is an extra key in `bt`).
- `/api/meta` carries it in `presets[k].bt` (no code change needed); the profile cards show it as a small warning tag
  above the existing backtest badge (`btBadge` in panel.html; the existing badge text is unchanged). The tag wraps, so the
  UI harness's sideways-scroll check should not be affected; the UI harness (Playwright) was not run here.
- `/api/research` gets one extra key, `unverified` (label + "every row that includes a DCA (dca_dip) slot"). Data only;
  the Research tab does not render it, and the research CSV/JSON files are not modified.

## Suites run (minipytest, file by file, each < 2 min)

| File | Result |
|---|---|
| tests/test_bt_intrabar_path.py | 14 passed (also 14 passed under the CP1252 shim) |
| tests/test_lab.py | 27 passed |
| tests/test_v31_engine.py | 45 passed |
| tests/test_grid.py | 14 passed |
| tests/test_safety.py | 78 passed |
| tests/test_fills.py | 46 passed |
| tests/test_ci.py | 36 passed |
| tests/test_verify.py | 16 passed, 1 known env-only failure (`test_summary_has_the_required_provenance_fields`) |
| tests/test_causality.py | 13/13 (per-case driver) |
| tests/test_leverage_auto.py, test_telegram.py, test_outage.py (engine.py touched) | 185 / 23 / 42 passed |

## Codex round 1 (PR #16 at 30a68e1: CHANGES REQUESTED, P1)

**Finding.** `run()` checked the stop as it stood at the open against the whole candle and exited before `walk_path()`.
On a monotonic adverse leg the market meets every DCA safety level before the deeper basket stop: live adds exposure
first, then the exchange stop closes the enlarged basket. Codex's fixture (long 100.02, safety 99.02 / 98.02 / 97.02,
stop 95.02, red candle O100 H100.2 L94 C99): the branch booked -0.209 R (first unit only).

**Fix (commit a1e7c82).**
- Only a **gap through the stop at the open** is handled before the walk: filled at the open, no adds.
- The stop (as of the open, or raised earlier in the candle) is an **adverse-leg event** queued with the safety orders in
  price order: every safety order met before the stop fills, then the stop closes the enlarged basket. Codex's fixture
  now books the 3 safety orders + stop: -1.039 R (short mirror -1.042 R; exact P&L asserted, incl. fees, slippage, funding). Symmetric for shorts.
- **Exact tie** (safety level == stop): the safety order fills first, then the stop - the worse outcome for the account.
- **Stop-first** is kept only for the ambiguous case it was meant for: when the open's stop is inside the candle's range,
  no target exit (basket TP, tp1, ladder, tp_r) is taken in that candle, even where the path reaches the target first.
  Adds are not suppressed: safety orders and pyramid adds still fire in path order before the stop.
- **Pyramid analogue**: the same precheck suppressed a pyramid add made on the way up of a red candle that then fell to
  the stop. Now: red candle -> add at the high, then the stop closes 1.5 units; green candle (low first) -> stopped
  before the add.
- **Trailing / breakeven stops set earlier in the candle vs adds**: a raised stop lying above the first safety order
  (long) is met first on the way down -> closed, no safety order. `pessimistic=False` (old v2) keeps testing only the
  open's stop inside the walk.

**New tests** (`tests/test_bt_intrabar_path.py`, 23 tests now, ~14 s; also 23/23 under the CP1252 shim):
`test_every_safety_order_on_the_way_down_fills_before_the_basket_stop_long` (Codex fixture), `..._up_..._short`,
`test_gap_through_the_stop_fills_at_the_open_without_adds` (long + short), `test_safety_level_exactly_at_the_stop_fills_first_then_the_stop`,
`test_target_and_stop_both_in_the_candle_the_stop_wins_after_the_adds`, `test_trailing_stop_raised_earlier_is_met_before_the_safety_orders_below_it`,
`test_pyramid_add_before_the_stop_on_a_red_candle_and_not_on_a_green_one`, and the live engine replay
`test_engine_and_backtest_agree_on_safety_orders_then_basket_stop_in_one_candle[long, short]` (one candle through every
safety order and the stop, 40 steps per leg: engine 3 safety orders + exchange stop, backtester the same, |dR| <= 0.1).
Against the round-0 code (b0a0e33) 8 of them fail (all but the gap case, which was already right).

**Mutation check: 15/15 killed** (M1-M9 from round 0, M6 now "stop never checked on the adverse legs", plus):

| Mutant | Failing tests |
|---|---|
| M10 whole-candle stop precheck restored | long/short basket-stop fixtures, tie, target+stop, trail vs safety orders, pyramid add before stop, engine-vs-bt [long, short] |
| M11 tie: the stop first | tie |
| M12 no stop-first for the ambiguous case | stop-before-target (round 0), target+stop |
| M13 gap through the stop not handled at the open | gap |
| M14 stop-first suppresses adds too | pyramid add before the stop |
| M15 the stop tested is always the open's (raised stop ignored) | pyramid red-candle trail, DCA runner breakeven, zero-length leg, trail vs safety orders |

**Causality**: 13/13 pass (per-case driver, 2 parallel). Parity metrics changed in one mode only:
"breakout pyramid + trail" p95 abs(dR) 0.070 -> 0.054, return gap 0.42 -> 0.54 pp, DD gap 0.38 -> 0.40 pp (same 11/11
matched trades); "DCA basket" and "trend long + pyramid" unchanged.

**Strict replays** (24 steps, strict gate on; tolerances unchanged):

| Replay | 874cfd8 / round 0 | round 1 (a1e7c82) | Gate (limit) | Strict |
|---|---|---|---|---|
| 1: MOM pyramid + DCA + Squeeze | matched 133/133, median 0.017, p95 0.118, return gap 2.81 pp, DD gap 0.03 pp | matched 133/133, median 0.015, p95 0.110, return gap 1.97 pp, DD gap 0.22 pp | 98 % / 0.05 / 0.25 / 3.0 / 2.0 | PASS |
| 2: breakout + bear shorts + Donchian | matched 300/301, median 0.019, p95 0.096, return gap 2.17 pp, DD gap 0.97 pp | matched 300/301, median 0.019, p95 0.072, return gap 1.80 pp, DD gap 1.33 pp | same | PASS |

Engine side unchanged (+54.9 % / -8.9 %); backtester +52.1 -> +53.0 % and -11.1 -> -7.1 %. Return gaps and p95 improve
in both; DD gaps grow but stay inside the 2.0 pp gate (replay 2: 1.33 pp).

**Core suites**: test_lab 27, test_v31_engine 45, test_grid 14, test_safety 78, test_fills 46, test_ci 36 passed;
test_verify 16 passed + the known env-only `test_summary_has_the_required_provenance_fields`.

### Presets (rerun after the round-1 fix)

The round-0 table above is superseded. Full old (874cfd8 backtester) vs new (this branch, round-1 fix) rerun:

| Preset | TF | Window | Final equity old -> new | PF old -> new | Max DD old -> new | Trades old -> new | Win % old -> new | 2022 old -> new | 2023 old -> new | 2024 old -> new | 2025 old -> new | 2026 old -> new |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| calm | 4h | 2022-01-25 to 2026-10-04 | $1,169 -> $1,141 | 2.21 -> 2.13 | -13.3% -> -13.3% | 595 -> 592 | 44.7 -> 44.1 | -7.1% -> -7.1% | 36.1% -> 36.2% | 31.6% -> 31.0% | 12.1% -> 11.0% | 25.3% -> 24.1% |
| balanced | 4h | 2022-01-25 to 2026-10-04 | $3,593 -> $3,066 | 2.34 -> 2.09 | -33.6% -> -33.9% | 864 -> 820 | 55.8 -> 52.1 | -19.7% -> -20.1% | 91.4% -> 90.5% | 104.5% -> 98.7% | 33.0% -> 24.4% | 71.8% -> 63.0% |
| aggressive | 4h | 2022-01-25 to 2026-10-04 | $6,916 -> $4,938 | 2.29 -> 1.96 | -45.2% -> -46.8% | 976 -> 927 | 60.2 -> 56.2 | -27.4% -> -29.4% | 128.9% -> 125.1% | 167.2% -> 147.6% | 51.5% -> 31.5% | 105.7% -> 90.8% |
| boost | 4h | 2022-01-25 to 2026-10-04 | $9,274 -> $3,943 | 2.65 -> 1.79 | -41.0% -> -45.6% | 821 -> 765 | 77.3 -> 73.5 | -26.6% -> -29.7% | 226.3% -> 205.9% | 95.3% -> 53.8% | 59.7% -> 12.7% | 148.4% -> 111.6% |
| active | 1h | 2022-09-04 to 2026-10-04 | $3,947 -> $1,294 | 1.18 -> 1.08 | -49.9% -> -61.4% | 4416 -> 4044 | 60.6 -> 55.7 | -24.6% -> -34.0% | 111.2% -> 53.3% | 88.8% -> 49.4% | 46.1% -> 15.9% | 79.7% -> 47.8% |
| active_dca | 1h | 2022-09-04 to 2026-10-04 | $4,637 -> $210 | 1.75 -> 0.8 | -25.9% -> -67.6% | 2491 -> 2034 | 94.1 -> 89.9 | 21.7% -> -16.9% | 133.1% -> -3.6% | 94.0% -> -12.8% | 49.9% -> -16.4% | 12.4% -> -28.2% |
| steady_mix | 4h half | 2022-01-25 to 2026-10-04 | $3,445 -> $2,868 | 2.57 -> 2.14 | -22.1% -> -23.1% | 773 -> 736 | 66.0 -> 62.4 | 1.0% -> 0.5% | 94.1% -> 92.3% | 109.9% -> 101.4% | 39.9% -> 29.5% | 19.7% -> 13.7% |
| steady_mix | 1h half | 2022-09-04 to 2026-10-04 | $4,637 -> $210 | 1.75 -> 0.8 | -25.9% -> -67.6% | 2491 -> 2034 | 94.1 -> 89.9 | 21.7% -> -16.9% | 133.1% -> -3.6% | 94.0% -> -12.8% | 49.9% -> -16.4% | 12.4% -> -28.2% |
| steady_mix | 4h+1h (50/50 mixed) | 2022-09-04 to 2026-10-04 | $4,015 -> $1,517 | 1.94 -> 1.56 | -14.7% -> -25.9% | 3264 -> 2770 | 87.5 -> 82.6 | 10.6% -> -8.9% | 115.5% -> 48.6% | 100.4% -> 67.6% | 45.6% -> 22.4% | 15.4% -> 9.3% |
| boost_active | 4h half | 2022-01-25 to 2026-10-04 | $9,274 -> $3,943 | 2.65 -> 1.79 | -41.0% -> -45.6% | 821 -> 765 | 77.3 -> 73.5 | -26.6% -> -29.7% | 226.3% -> 205.9% | 95.3% -> 53.8% | 59.7% -> 12.7% | 148.4% -> 111.6% |
| boost_active | 1h half | 2022-09-04 to 2026-10-04 | $3,947 -> $1,294 | 1.18 -> 1.08 | -49.9% -> -61.4% | 4416 -> 4044 | 60.6 -> 55.7 | -24.6% -> -34.0% | 111.2% -> 53.3% | 88.8% -> 49.4% | 46.1% -> 15.9% | 79.7% -> 47.8% |
| boost_active | 4h+1h (50/50 mixed) | 2022-09-04 to 2026-10-04 | $8,112 -> $3,256 | 1.51 -> 1.3 | -32.5% -> -43.8% | 5237 -> 4809 | 63.2 -> 58.5 | -13.8% -> -20.6% | 176.2% -> 142.8% | 93.1% -> 52.6% | 55.3% -> 13.5% | 127.3% -> 94.9% |

Rerun finished (background job, frozen copy of ffca8f0 vs the 874cfd8 backtester; data_long, core 8, research_long/long2 jobs). `original` (no DCA slot) is unchanged.

## For Codex to decide

1. **Doji policy**: worst case per position side (favourable first). No measured preset impact; replay.py follows it.
2. **Stop-first** (round 1): kept only as "the open's stop inside the candle -> no target exit in that candle"; adds
   and the stop itself follow the path. Exact tie safety level == stop: the safety order fills first.
3. **Fresh-ATR trail timing**: kept as before (after the first leg). Ratcheting at the bare open would follow the engine
   but fails strict replay 2's return gap (3.16 > 3.0 pp) on a scenario without DCA; that is an engine/backtest timing
   gap that predates this ticket (the engine also needs a minimum step of 0.1 ATR before it moves a stop, the
   backtester does not).
4. **Ties** inside one leg: stop before a safety order; favourable ties basket TP < add < tp1 < ladder < tp_r.
5. **Re-baselining**: the DCA profiles' quoted numbers (notes / `bt`) stay as they were, marked unverified. Re-validation
   (new numbers in the notes, or retiring/reworking `active_dca` and the DCA slots of the mixes) is a separate decision.
6. Not changed: `lab.liquidation` exposure estimate (approximate DCA/pyramid qty from running extremes, not event
   booking) and the grid backtester (`grid.py` / `research_grid.py`, no DCA presets).

## Rollback

Revert the commits on this branch (backtest.py / replay.py / the test, and the label commit). No state or settings
format changed.
