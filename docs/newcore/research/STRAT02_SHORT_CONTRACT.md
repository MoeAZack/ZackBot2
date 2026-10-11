# STRAT-02 contract: `trend_ema_mom_short.v1` (short-only trend continuation)

**Status: UNREGISTERED DRAFT for Codex review.** It is not frozen and not runnable. No ledger record exists, no short-side code exists, and the R4 gate does not point at it. The machine-readable twin is `research_evidence/prereg/trend_ema_mom_short.v1.json`. If the two ever disagree, the JSON is authoritative once frozen.

Author: claude-code. Date: 2026-10-11 (Cairo). Base: master `e2e39e98fab27e7f52d1986a4a5c87421de2cf75`.

## 0. What this is and what it is not

- **Scope:** one fresh, short-only, trend-continuation candidate on the same accepted R4b engine, book, universe, splits and costs as STRAT-01. It is a separate family (`trend_ema_mom_short`) with its own ledger.
- **No outcomes were computed.** No historical result, ranking, tuning or data read went into this contract.
- **Parameters come from sign symmetry, not from fitting.** Every parameter is fixed a priori by exact sign-symmetry with the registered `trend_ema_mom.v1`.
  - STRAT-01 detail files and per-symbol or per-split results were not consulted.
  - The author has seen only STRAT-01's published sealed aggregates. A long rule's aggregate cannot inform the short rule's parameters, and none was used.
- **No code is implemented here.** The required runner slice is listed in §10.

## 1. How it differs from the rejected 4h BTC-bear Donchian short

The rejected candidate is #13 comment 6055348078 (`short_research/PREREG.md`, 2026-10-08). It failed 3 of its 4 preregistered criteria: 3x-funding robustness, concentration, and holdout > 0.

| | Rejected Donchian breakdown | `trend_ema_mom_short.v1` |
|---|---|---|
| Trigger | close below the prior 20-bar low (price-level breakout) | EMA20 crosses below EMA50 (trend-state change); no breakout level |
| Regime | **BTC-bear gate** (BTC 4h close < EMA200) required to enter | **per-symbol** filter: the symbol's own close < EMA200 and 7d and 30d returns < 0. No BTC gate; the BTC label is used only for reporting and B0 matching |
| Exit | close above the prior 10-bar high | trend or momentum loss (EMA20 > EMA50, or either return >= 0), at the next open |
| Stop / horizon | fixed 2x ATR14, no time cap | 2.5x ATR14, ceiled to the tick, gap-at-open; **180-bar cap** at the next open |
| Universe | PIT volume filter, core8 | frozen static-40 list; CORE-10 / CORE-20 tiers reported separately |
| Book | no portfolio cap (up to 8 open) | the registered book: max 4 lots, 3x gross, 1% actual-risk sizing, daily halt, mainnet lot rules |
| Funding | real for BTC/ETH only; the rest used an ETH proxy | actual per-symbol funding at each funding time × the mark proxy, fail-closed; `funding_x2` stress row |
| Concentration | checked after the fact (it failed) | LOSO / LOYO and neighbour-stability rows are **gating** EDGE-00 rows |
| Holdout | 2025-2026 history (now **spent** for the breakdown family) | a **fresh forward** holdout (2026-10-19 → 2027-10-18) that nobody has seen |

**Why the differences matter.**
- The Donchian rule fired on a single price-level break inside a BTC-bear window. Its edge then came from a few crash episodes (AVAX 61%, Apr–Jul 2022 105% of net R).
- This rule needs a persistent, multi-horizon downtrend in the symbol itself. It also exits when that state ends, not on a fixed channel.
- The concentration, funding and forward-holdout weaknesses that killed the Donchian candidate are written into this contract as gates, before any data is read.
- None of these choices was made by looking at the Donchian results' per-symbol or per-period numbers. Only its published verdict and failure modes were used.

## 2. Economic rationale (general mechanisms only, no fitted numbers)

1. **Time-series momentum.** Returns are documented to persist across asset classes over weeks to months. The mechanisms are slow information diffusion and investor under-reaction followed by trend-following flows. Nothing about these mechanisms is long-only.
2. **Forced deleveraging in crypto perps.** Long-biased leverage is common. Falling prices trigger margin calls and liquidations, which are market sells that push price further down. This feedback is strongest after a downtrend is already established, which is what the entry filter requires.
3. **Risk-off de-grossing.** Correlated selling across the market, when the sizing and risk budgets of crypto funds and market makers contract.
4. **Funding cuts both ways and is charged honestly.** A short receives positive funding (crowded longs) and pays negative funding (crowded shorts). Late-stage downtrends often carry negative funding, so the contract charges actual signed funding and stresses it at 2x.
5. **Known headwinds, stated up front.**
   - Short squeezes cause upward gaps through the stop; these are modelled as gap fills at the open.
   - The frozen current-survivor universe excludes delisted collapses, which likely biases results **against** shorts.
   - Crypto's long-run upward drift works against shorts.

## 3. Exact fixed rules (4h, decided at bar close, filled at the next open)

| Item | Rule |
|---|---|
| Entry `se` (close of bar i) | `cross_down(EMA20, EMA50)` and `close < EMA200` and `ret7d < 0` and `ret30d < 0`. Market **sell** at the open of bar i+1 (taker; adverse slip-v1 puts the fill below the open). |
| Exit `sx` (close of bar i) | `EMA20 > EMA50` or not (`ret7d < 0` and `ret30d < 0`). Market **buy** (cover) at the open of bar i+1 (taker; fill above the open). |
| Stop | `fill + 2.5 × Wilder ATR14(signal bar)`, **ceiled** to the tick. A bar that **opens at or above** the stop fills at its open (gap). Otherwise, a bar whose **high** reaches the stop fills at the stop, plus adverse slip. The stop is checked first on every played bar. |
| Time cap | A lot still open 180 bars after entry is covered at the **next bar open**. At a split end, the causal `View.next_open` supplies it; with no next open inside the split the trade is INCOMPLETE, never filled at a close. |
| Exit time for funding | Signal and cap exits and gap stops exit at the bar open. A non-gap intrabar stop exits at the stop-bar **close** (PR #61 / #67 convention), with the fill at the stop level. |
| Funding | Signed actual funding at each funding time T with entry <= T < exit time, times funding-mark proxy v1. **A short receives a positive rate and pays a negative one** (`costs.funding_cost(side='short')`). A missing mark or an unprovable sequence makes the trade INCOMPLETE. |
| MFE / MAE (diagnostics) | MFE = entry − lowest low before the exit. On the stop bar the bound is conservative: earlier lows plus the stop bar's **open**, never its own low. MAE uses highs, with the stop bar bounded by the stop. |
| Indicators | Identical to v1: EMA `adjust=False` seeded at bar 0, Wilder ATR seeded at TR0, 7d = 42 and 30d = 180 bars, the last 1500 closed bars, warm-up 220 bars. Plus `cross_down(a,b) = a_i < b_i and a_(i-1) >= b_(i-1)`. |
| Orders | Taker only; no limit orders. One lot per symbol. Within a cycle, exits come first, then entries in frozen-list rank order. |

**Parameters (all fixed by symmetry with v1):** ema 20/50/200, ATR 14, stop 2.5, momentum 7d/30d, warm-up 220, cap 180, window 1500.

## 4. Risk and sizing: the same registered book

The book is run separately on its own 10,000 USDT, never pooled with STRAT-01.
- **Risk per trade:** 1% of the cycle's wallet snapshot, divided by the **actual** per-unit risk. That is the ceiled stop minus the tick-rounded fill (the short mirror of the #67 fix).
- **Limits:**
  - max 4 open lots;
  - gross notional <= 3x equity, sized on signal close × 1.10;
  - a 3% MTM halt per Cairo day;
  - drawdown kill disarmed.
- **Quantity rules:**
  - quantity is floored to the step and never upsized;
  - min qty and min notional are checked on qty × the **executable fill**;
  - anything below them is a counted refusal;
  - a non-positive or non-finite stop risk is refused.
- **Lot rules:** the Binance **mainnet** pin, `data/exchange_rules_mainnet.json` (sha256 `a34ec711…0aa3`, PR #69). The M3 testnet anchor is never used.
- **Liquidation:** not modelled as an exit (cross margin, gross <= 3x). It is reported as a diagnostic: the largest one-bar adverse excursion of any open short, plus any bar where book MTM equity would fall below 1/3 of gross notional. **Any such bar is a PARK flag.**

## 5. Costs and stress rows

These are unchanged from v1:
- the `zb-cost-model/2` crypto row: VIP0 taker 0.05% per side, slip-v1 TR-SMA14 with c = 0.05 (SLIP-C-UNFITTED), and actual signed funding;
- delisting: exit at the next open after a timestamped notice, else the last mark with 5x slip, reported separately;
- stress rows: `base`, `fees_slip_x2`, `funding_x2`, `gap_slip_x5`, `limit_no_fill`, `limit_as_taker`, `flat_funding_legacy`, `slip_wilder_atr14` and `funding_mark_adverse`, each running its own book.
- For shorts, `funding_x2` doubles the signed funding. It hurts when funding is negative and helps when it is positive. `funding_mark_adverse` moves the mark against the short.

## 6. Hypotheses and mechanical verdict

- **H1 (primary):** the sealed forward-holdout, episode-clustered mean net R per trade of the capped short primary (base row) is greater than 0, with the lower 95% CI above 0.
- **EDGE-00 rows:** identical to v1, except that multiplicity uses the program family (§8).
  - Sample: >= 60 episodes, >= 30 holdout episodes, >= 6 month blocks.
  - Expectancy: holdout-only CI.
  - Baselines: Holm p < 0.05 against each of B0, B1 and B2.
  - Robustness: LOSO and LOYO means > 0.
  - Neighbours: >= 80% of neighbours keep the sign.
  - Intrabar: <= 5% ambiguous trades.
  - E10 and E11: PARK-only.
- **PROMOTE:** only on the sealed forward holdout, and only when every EDGE-00 row passes. INCOMPLETE trades never count.
- **REJECT:** a powered holdout (>= 30 holdout episodes) with a base-row mean <= 0.
- **PARK:** everything else, including a positive mean whose CI includes 0, any failing row, unfitted or provisional cost rows, and a liquidation flag (§4).
- **Development runs** (any run before the holdout opens) can neither PROMOTE nor REJECT. A base-row development mean <= 0 is flagged DEV-NEGATIVE. Nothing is retuned either way.

## 7. Baselines (short analogues; seed 20261012)

| | Definition | Pairing |
|---|---|---|
| B0 | 1,000 matched random **short** entries per trade. Each matches the trade's symbol, side, split and PIT regime label (BTC 4h close vs EMA200). Each uses the trade's realised hold capped at 180 (exit at the next open), the same ceiled gap-at-open stop, the same costs and the short funding sign. Invalid draws are counted. | episode |
| B1 | Equal-weight **short** of the list members with data at the time, rebalanced each Monday 00:00 UTC, short funding sign, scaled to equal risk (1R = 1% of equity). | calendar-month blocks |
| B2 | 20-bar momentum **short**: short when close < close 20 bars earlier, cover when not. Same stop, cap, costs, sizing and book. | calendar-month blocks |
| B3 | Cash (0 R); reduces to the expectancy row. | — |

The test is a one-sided paired sign-flip test with 10,000 permutations, Holm-adjusted over the program family (§8). Ordinary 95% CIs are reported beside it.

## 8. Trial accounting (declared up front)

- **Variant:** 1, the capped primary `trend_ema_mom_short.v1.cap180`. There is no uncapped variant because there is no M3 short reproduction.
- **Grid points:** 12 one-at-a-time ± one-step neighbours, with steps identical to v1: ema_fast ±5, ema_slow ±10, ema_trend ±50, stop_atr ±0.5, mom_short_days ±2, mom_long_days ±10. They feed only the 80% stability row; nothing is selected from them.
- **Baselines:** 4 (B0–B3).
- **Total in this family: 17.**
- **Gating Holm family:** this family's 17 plus the 18 records of `trend_ema_mom` (STRAT-01), giving **n = 35** at freeze. Any later record of either family raises it. The own-family n of 17 is reported beside it but never gates.
- **Tiers:** CORE-10, CORE-20 and EXT-21-40 are reporting cuts of the same trials and add no trials.

## 9. Frozen inputs

| Input | Value |
|---|---|
| Store manifest | `binance-um-archive-v1`, digest `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7` |
| Universe | `static-top40-minhist365-contract-20261011`, digest `1c06c18d…3fce`, list digest `d95ba63c…c5`, labelled CURRENT-UNIVERSE / SURVIVOR-BIASED |
| Tiers | CORE-10 = ranks 1-10, CORE-20 = ranks 1-20 (run in that order, CORE-20 unchanged); EXT-21-40 reported later |
| Exchange rules | `data/exchange_rules_mainnet.json`, sha256 `a34ec71130683101183cd6039fcdd37096457544ccb9c07bbcdaa09d90680aa3` (mainnet, direct fetch) |
| Splits | Identical to v1 (4h; purge 240, horizon 180, embargo 1). Calibration 2020-02-03..2021-01-04 is excluded. Train 2021-01-04..2021-12-20. Walk-forward folds 0–4 end 2026-09-28. |
| Holdout | **2026-10-19T00:00Z → 2027-10-18T00:00Z** (forward, sealed, opened once and atomically). 2026-09-28 → 2026-10-19 is never read. |
| Costs | `zb-cost-model/2`, slip-cal-v1 (c = 0.05 crypto, unfitted), the 9 stress rows above |
| Seeds | 20261012 (B0, bootstrap, permutations) |

**Why the holdout differs from STRAT-01's.** `register` must precede the holdout start, and STRAT-01's window opens 2026-10-12, before this contract can be reviewed and frozen. The holdout is therefore the next Monday. If registration misses 2026-10-19T00:00Z, this draft is void and a new version uses the following Monday. A window is never back-dated.

## 10. Work this contract implies (not done here; needs Codex release)

1. **Short-symmetry runner slice** in `pit_eval` / `pit_run` (no other module):
   - `se`/`sx` signals plus `cross_down`;
   - a sell entry with an adverse fill and a ceiled stop;
   - stop-at-or-above-open gaps and high-based stop hits;
   - short funding sign over the same window;
   - mirrored MFE/MAE bounds;
   - a short B0 / B1 / B2;
   - actual-risk sizing and fill-based min notional for shorts;
   - the liquidation diagnostic.
   It comes with synthetic tests only (Evidence has pre-staged the adversarial cases).
2. **Generalise `run_r4.validate_prereg` and `register`.** They currently hard-code `trend_ema_mom.v1` (family, variant names, 18 trials, M3 manifest). Against this draft they report exactly those 5 identity findings and nothing structural. They also need to compute the program-level Holm n across both family ledgers.
3. **Point the R4 gate** (`r4_gate.json`) at this prereg only after Codex freezes it.

## 11. Open questions for Codex

1. **Program Holm n = 35** (this family plus STRAT-01) versus own-family n = 17: is the cross-family gating family the right control? It is the more conservative choice.
2. **Holdout start 2026-10-19:** is it acceptable for this family to have a different forward window from STRAT-01's, or should both families share one later window?
3. **Liquidation:** a diagnostic plus a PARK flag, or an explicit liquidation exit model?
4. **B1 hold-short** ignores borrow (perps have none) but carries short funding. Is that analogue acceptable as the passive benchmark?
5. **The LF test change:** the single test edit lists the new prereg file in `test_committed_prereg_artifacts_are_lf_in_every_checkout`. Is that acceptable, or should drafts live outside `research_evidence/prereg/` until frozen?
