# STRAT-02 contract: `trend_ema_mom_short.v1` (short-only trend continuation)

**Status: FROZEN v1 (REG-02, Codex 6155928419, frozen 2026-10-14 Cairo).** The canonical, authoritative machine-readable preregistration is `research_evidence/prereg/trend_ema_mom_short.v1.json` (LF bytes; it replaces the former non-canonical draft under `docs/newcore/research/drafts/`). It is registered by `run_r4.py register --candidate trend_ema_mom_short.v1` through the separate short gate `research_evidence/prereg/r4_gate_short.json` into its own root-family ledger `research_evidence/ledger/trend_ema_mom_short.jsonl` (§12). If this text and the JSON ever disagree, the JSON is authoritative. Missing data follows the accepted policy `zb-missing-data/1` (§3.2).

Author: claude-code. Drafted 2026-10-11 (Cairo), frozen 2026-10-14 (Cairo). Draft base: master `e2e39e98fab27e7f52d1986a4a5c87421de2cf75`; freeze base: master `7af6623338761010e4479fa54d09b2265867a4af`.

## 0. What this is and what it is not

- **Scope:** one fresh, short-only, trend-continuation candidate on the same accepted R4b engine, book, universe, splits and costs as STRAT-01. It is a separate family (`trend_ema_mom_short`) with its own ledger.
- **No outcomes were computed.** No historical result, ranking, tuning or data read went into this contract.
- **Parameters come from sign symmetry, not from fitting.** Every parameter is fixed a priori by exact sign-symmetry with the registered `trend_ema_mom.v1`.
  - STRAT-01 detail files and per-symbol or per-split results were not consulted.
  - The author has seen only STRAT-01's published sealed aggregates. A long rule's aggregate cannot inform the short rule's parameters, and none was used.
- **No code is implemented here.** The required runner slice is listed in §10.

## 1. How it differs from the earlier BTC-bear Donchian short (structural comparison only)

The earlier candidate is #13 comment 6055348078 (`short_research/PREREG.md`, 2026-10-08), which was not accepted. This section compares **rule structure only**. No outcome of that candidate (per-symbol, per-period or aggregate) was used to design this contract, and none is cited.

| | Earlier Donchian breakdown | `trend_ema_mom_short.v1` |
|---|---|---|
| Trigger | close below the prior 20-bar low (price-level breakout) | EMA20 crosses below EMA50 (trend-state change); no breakout level |
| Regime | **BTC-bear gate** (BTC 4h close < EMA200) required to enter | **per-symbol** filter: the symbol's own close < EMA200 and 7d and 30d returns < 0. No BTC gate; the BTC label is used only for reporting and B0 matching |
| Exit | close above the prior 10-bar high | trend or momentum loss (EMA20 > EMA50, or either return >= 0), at the next open |
| Stop / horizon | fixed 2x ATR14, no time cap | 2.5x ATR14, ceiled to the tick, gap-at-open; **180-bar cap** at the next open |
| Universe | PIT volume filter, core8 | frozen static-40 list; CORE-10 / CORE-20 tiers reported separately |
| Book | no portfolio cap | the registered book: max 4 lots, 3x gross, 1% actual-risk sizing, daily halt, mainnet lot rules |
| Funding | real for BTC/ETH; an ETH proxy for the rest | actual per-symbol funding at each funding time × the mark proxy, fail-closed |
| Parameters | its own choices | the exact sign-mirror of the registered `trend_ema_mom.v1` (§3) |

**Evidence status.** The shared historical window (2021-01-04 → 2026-09-28) is **DEVELOPMENT-ONLY** for this family, whatever earlier research has or has not looked at. Development results can neither PROMOTE nor REJECT. **The fresh forward holdout (2026-10-19 → 2027-10-18) is the only untouched claim.**

The gating rows (LOSO/LOYO, neighbour stability, `funding_x2`, the program Holm family) are the standard v1 EDGE-00 rows applied unchanged to the short side. They are not designed around any earlier result.

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
| Time cap | A lot still open 180 bars after entry is covered at the **next bar open**. At a split end, the causal `View.next_open` supplies it; with no next open inside the split the lot is split-end censored (`incomplete.split_end`, §3.2): INCOMPLETE, never filled at a close, no halt. |
| Exit time for funding | Signal and cap exits and gap stops exit at the bar open. A non-gap intrabar stop exits at the stop-bar **close** (PR #61 / #67 convention), with the fill at the stop level. |
| Funding | Signed actual funding at each funding time T with entry <= T < exit time, times funding-mark proxy v1. **A short receives a positive rate and pays a negative one** (`costs.funding_cost(side='short')`). A funding-rate sequence that cannot be proved complete through the played bar (`incomplete.funding`, class funding_rate_gap) and a funding event without a mark (`incomplete.funding_mark`, class funding_mark_gap) are distinct classes; either halts the row (§3.2). |
| MFE / MAE (diagnostics) | MFE = entry − lowest low before the exit. On the stop bar the bound is conservative: earlier lows plus the stop bar's **open**, never its own low. MAE uses highs, with the stop bar bounded by the stop. |
| Fill tick rounding | Applied after adverse slip-v1. A **sell** (short entry) fill is rounded **DOWN** to the tick. A **buy** (cover: signal, cap, stop, gap stop or delist exit) fill is rounded **UP** to the tick. The stop level is **ceiled** to the tick. Every risk, sizing, PnL and R figure uses these exact rounded values, never unrounded ones. |
| Indicators | Identical to v1: EMA `adjust=False` seeded at bar 0, Wilder ATR seeded at TR0, 7d = 42 and 30d = 180 bars, the last 1500 closed bars, warm-up 220 bars. Plus `cross_down(a,b) = a_i < b_i and a_(i-1) >= b_(i-1)`. |
| Orders | Taker only; no limit orders. One lot per symbol. Within a cycle, exits come first, then entries in frozen-list rank order. |

### 3.1 Window and EMA-seed behaviour (intentionally identical to STRAT-01)

Each decision is a deterministic function of the closed 4h bars **as read**:
- the last `min(1500, contiguous tail)` bars available at the decision;
- every EMA seeded at the first bar read and the Wilder ATR at that bar's TR;
- a 220-bar warm-up.

This is **intentionally identical to STRAT-01** (`trend_ema_mom.v1`). The window is part of the rule definition. It is accepted on these exact terms, which the later runner must enforce with tests:

1. **Exact prefix invariance (gating test).** Changing, adding or removing any bar *before* the read window never changes a decision. The decision reads nothing outside its window. Required: bit-identical `se` / `sx` / stop-distance outputs.
2. **Declared tolerance versus longer history (stated, not a decision rule).** The seed's residual weight after k bars is `((n−1)/(n+1))^k` for an EMA of length n.
   - With a full 1500-bar window, the EMA200 residual is ≈ 3.1e-7. For the widest neighbour EMA250 it is ≈ 6.2e-6, and it is negligible for EMA20/50 and ATR14.
   - Required test: on synthetic fixtures with a full window, prepending 1500 earlier bars changes every indicator by at most **1e-5 relative**.
   - Real-data decisions are always the as-read window's. They are never recomputed with longer history, and no tolerance is applied to a decision.
3. **Early-history dependence is accepted by design.** Before the window reaches 1500 bars (from the 220-bar warm-up onward, where the EMA200 residual is ≈ 0.11), decisions depend on where the contiguous history starts. This is the same declared behaviour as STRAT-01, not a defect to be tuned.

**Parameters (all fixed by symmetry with v1):** ema 20/50/200, ATR 14, stop 2.5, momentum 7d/30d, warm-up 220, cap 180, window 1500.

### 3.2 Missing data: accepted policy `zb-missing-data/1` (M1, PR #74)

The frozen JSON binds the policy in its `missing_data` block (policy id + `docs/newcore/research/MISSING_DATA_POLICY_v1.md`). In short:
- **One causal pass per book row.** No re-run and no symbol exclusion; a later gap never rewrites the universe, earlier trades, cash or capacity.
- **First unaccountable event wins.** Its cycle is rolled back and the **whole row halts** there (status INCOMPLETE, `first_unaccountable_ms` / `last_valid_ms`, a row censor with wallet + unrealised = MTM). Nothing is forward-filled, flat-lined or forced to a mark.
- **Distinct classes:** missing_bar (`incomplete.gap`), delisting (`incomplete.delisted`), funding_rate_gap (`incomplete.funding`), funding_mark_gap (`incomplete.funding_mark`), split_censoring (`incomplete.split_end`).
- **One primary per row, deterministically** (shared book: frozen symbol order; unit book: cycle, then phase bar < exit < next_open/fill < funding < decision, then symbol). Other open lots are the secondary `incomplete.book_halt`. Decisions at the halt cycle are not booked.
- **Delist:** with a timestamped notice, a held short is covered at the first bar open **strictly after** the notice (`delist_cover`) and no entry is taken once it is known. With no notice, the end of a held series is `incomplete.delisted`: INCOMPLETE, no fake fill.
- **Exact slippage window:** a fill at open `bo` uses exactly the bars closing at `bo − (n−1)·4h .. bo`; a hole inside it is `incomplete.gap`.
- **B1 exact decision window:** see §7.1. No membership shrink; the execution open is required only for a non-zero delta.
- **Split end:** a lot open at a split end with no observable next open is censored (`incomplete.split_end`), with no halt.
- **Flat-symbol gaps** leave the row COMPLETE only when no later decision, sizing, fill, position or accounting depends on them.
- **Fail closed:** every consumer (runner, report gate, readiness) uses the one predicate in `tools/research/missing_data.py`. An INCOMPLETE row makes its tier INCOMPLETE (verdict INCOMPLETE), sealed for audit with no evidence.

## 4. Risk and sizing: the same registered book

The book is run separately on its own 10,000 USDT, never pooled with STRAT-01.
- **Risk per trade:** 1% of the cycle's wallet snapshot, divided by the **actual** per-unit risk `stop − fill`. Here `fill` is the slipped sell fill rounded DOWN to the tick and `stop = ceil_tick(fill + 2.5 × ATR14)`, both exactly as booked (§3). This is the short mirror of the #67 fix.
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
- **Liquidation:** this is a **PARK-only diagnostic, never an exit** (Codex ruling). On every played 4h bar t with open shorts:
  - Gross notional is `G_t = Σ_s q_s × high_s,t`.
  - Worst-case equity is `W_t = wallet_t + Σ_s q_s × (entry_s − high_s,t)`. Here `wallet_t` is the realised wallet after the fees and funding booked up to t, and every open short is marked at its own bar high at the same time, ignoring stops.
  - The bar is flagged when `W_t < G_t / 3` (worst-case equity below the 3x initial margin).
  - Reported:
    - the count of flagged bars;
    - `min_t W_t / G_t`;
    - the largest single-bar `(high − open) / open` of any open short.
  - **Any flagged bar is a PARK flag for Codex.** It never changes an exit, a fill or a sizing decision.
  - **Sensitivity is intentional.** As gross notional approaches the 3x cap, `G_t / 3` approaches equity, so the diagnostic flags more readily. It stays an observation and a PARK flag only. It is never an exit, never a hidden leverage constraint, and never an input to sizing.

## 5. Costs and stress rows

These are unchanged from v1:
- the `zb-cost-model/2` crypto row: VIP0 taker 0.05% per side, slip-v1 TR-SMA14 with c = 0.05 (SLIP-C-UNFITTED), and actual signed funding;
- delisting (reconciled to `zb-missing-data/1` at freeze): cover at the first bar open strictly after a timestamped notice, with no entry once a notice is known; with no notice the end of a held series is `incomplete.delisted` (INCOMPLETE, never a fake fill or last-mark close); forced-exit PnL reported separately;
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
- **INCOMPLETE (missing data, §3.2):** an INCOMPLETE row or tier is not evidence of any kind (not PARK evidence either): its verdict is INCOMPLETE and it is sealed for audit with no evidence.
- **Development runs** (any run before the holdout opens) can neither PROMOTE nor REJECT. A base-row development mean <= 0 is flagged DEV-NEGATIVE. Nothing is retuned either way.

## 7. Baselines (short analogues; seed 20261012)

| | Definition | Pairing |
|---|---|---|
| B0 | 1,000 matched random **short** entries per trade. Each matches the trade's symbol, side, split and PIT regime label (BTC 4h close vs EMA200). Each uses the trade's realised hold capped at 180 (exit at the next open), the same ceiled gap-at-open stop, the same costs and the short funding sign. Invalid draws are counted. | episode |
| B1 | Equal-weight **perp short** basket of the list members; exact definition in §7.1. | calendar-month blocks |
| B2 | 20-bar momentum **short**: short when close < close 20 bars earlier, cover when not. Same stop, cap, costs, sizing and book. | calendar-month blocks |
| B3 | Cash (0 R); reduces to the expectancy row. | — |

### 7.1 B1 exact definition (perp short benchmark, Codex ruling)

B1 is a USD-M perpetual short. It has no spot borrow and no borrow fee, and it carries signed actual funding: it receives positive rates and pays negative ones. B1 runs its own book, starting at 10,000 USDT in each split.
- **Rebalance times τ:** every Monday 00:00 UTC inside the split's decision range, plus the first 4h bar of that range.
- **Members M_τ:** the frozen-list members of the tier with a closed 4h bar at τ and an ATR14 defined on the contiguous tail, known at the decision close. A member not listed yet (no bar before τ) is warm-up, not missing. **Exact decision window (`zb-missing-data/1`):** a listed tier symbol whose decision candle at τ or exact hole-free `T.WINDOW`-bar input read has a hole halts the whole row at τ (phase decision); the membership never shrinks or shifts silently. The execution open is **not** a membership filter. N_τ = |M_τ|. With N_τ = 0, B1 is flat until the next τ.
- **Target quantity per symbol:** with E_τ the B1 book's MTM equity at τ, set `q*_s = floor_step( (0.01 × E_τ / N_τ) / (2.5 × ATR14_s(τ)) )`. The whole basket thus risks 1% of equity across 2.5-ATR moves, the same risk unit as the candidate.
- **Leverage cap:** if `Σ q*_s × close_s(τ) × 1.10 > 3 × E_τ`, every `q*_s` is multiplied by the common factor `3 × E_τ / Σ(...)` before flooring.
- **Minimums:** a leg below min qty or min notional (checked on qty × fill, mainnet pin) is set to 0 and counted.
- **Trades:** at the open of the bar starting at τ, each symbol trades only the difference `q*_s − q_s` (sell to add short, buy to reduce), taker, with adverse slip-v1. A member that leaves M_τ is fully covered. The execution open is required only for a non-zero delta `|q*_s − q_s|` (a flat member whose target floors to 0 needs none); a decided leg without its open halts the row (phase next_open).
- **What B1 omits:** no stop, no time cap and no other exits. Funding is charged on the held quantity at each funding time; a funding-rate or mark gap of a held leg halts the row (funding_rate_gap / funding_mark_gap), never a symbol exclusion. An INCOMPLETE B1 row is not evidence.
- **Block result:** for each calendar month m, `B1_R(m) = (E_end(m) − E_start(m)) / (0.01 × E_start(m))`, from MTM equity at the month boundaries.
- **Pairing:** with the candidate's block net R for the same month in the same split, via its book's MTM in the same units.

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
3. **Move the frozen JSON** to `research_evidence/prereg/trend_ema_mom_short.v1.json` (and extend the committed-prereg LF test list then), and point the R4 gate at it, only after Codex freezes it. **Done in REG-02 (§12)**, with a separate short gate instead of repointing the STRAT-01 gate.

## 11. Codex rulings recorded (6112077638)

1. **Multiplicity:** the program Holm family of n = 35 (this family's 17 plus STRAT-01's 18) is retained as the gating family. The own-family n of 17 is reported only.
2. **Holdout:** the fresh forward holdout 2026-10-19T00:00Z → 2027-10-18T00:00Z is valid if registration lands before it. Otherwise this draft is void (§9).
3. **B1:** a USD-M perp short benchmark with signed funding and no spot-borrow assumption, with exact sizing and rebalancing in §7.1.
4. **Liquidation:** a PARK-only diagnostic (§4), not an exit model.
5. **Draft location:** the draft JSON is non-canonical (`docs/newcore/research/drafts/`). Only a Codex-frozen prereg moves to `research_evidence/prereg/`, immediately before registration. The committed-prereg test list is unchanged. (Superseded at freeze by REG-02, §12.)

## 12. Freeze and registration (REG-02, Codex 6155928419)

- **Canonical prereg:** `research_evidence/prereg/trend_ema_mom_short.v1.json`, LF bytes (`.gitattributes` `research_evidence/prereg/*.json text eol=lf`). The only changes from the reviewed draft are the status / freeze date and the reconciliation of the missing-data text to `zb-missing-data/1` (rule.time_cap, rule.funding, costs.delist, baselines.B1, splits.notes, verdict_rules.PARK and the new `missing_data` block). Signal parameters, trials, universe, costs, seeds, splits and verdict thresholds are unchanged.
- **Separate short gate:** `research_evidence/prereg/r4_gate_short.json` (`candidate_id` routing, exact `prereg_sha256`, the short ledger, the frozen universe, and the accepted source + integrated commits of STRAT-02 #73 and M1 #74). The STRAT-01 gate `r4_gate.json` is unchanged.
- **Registration:** `python tools/research/run_r4.py register --candidate trend_ema_mom_short.v1 --author claude-code --cairo-date 2026-10-14` appends exactly 17 records (1 capped primary, 12 neighbours, B0-B3; no uncapped variant, no M3) to the new root family `trend_ema_mom_short` (one `REGISTRY.jsonl` family entry, `parent: null`). It refuses at or after the holdout start 2026-10-19T00:00:00Z, and a second registration is a no-op refusal.
- **Multiplicity:** the gating program Holm family is `trend_ema_mom` + `trend_ema_mom_short` trial records = 18 + 17 = **35** at registration (the short gate's G3 requires >= 35; later records only raise it). The own-family 17 is reporting only.
- **Deadline:** the 2026-10-19 holdout stays valid only if the prereg, ledger and registry records land in master before 2026-10-19 03:00 Cairo (00:00 UTC). Otherwise a reviewed later version moves the entire window to a later Monday; never back-dated.
- **Not in this ticket:** any historical run, development result or holdout access. Short real-environment evaluation wiring (`pit_run.real_env` / `evaluate_tier` for short CORE-10) is the next ticket.
