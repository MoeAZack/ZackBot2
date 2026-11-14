# BASKET-OLD3-H5: shared-account comparison contract draft (prep only; no counts or outcomes)

Status: DRAFT for Codex contract GO (PR #337 6832035130). Research only. This is a portfolio study of two existing consumedDEV leads.
It is not a new alpha, a holdout or an activation case. Items marked **[C]** need Codex to confirm or pin them before the freeze.

## 1. Existing evidence check (what can be reused, and what is still missing)
| Evidence | What it gives | Why it does not already answer the question |
|---|---|---|
| Codex duplicate tie-order replay (`codex_duplicate_tie_order_20261112/overlay_sim.py`, evidence b0447341; tapes 5121e791) | Shared-account OLD3 replay, 40 orders | OLD3 only, no H5. R × risk-fraction (no executable quantities). 4h bars from the 15m cache. $16 risk |
| Claude PR #339 overlay v2 | Same question as above, agrees with Codex | Same limits |
| H5 canary evidence (`codex_c1h_20261112/h5_canary_evidence`, run_case via `backtest.run`, 9 coins, 220 closes) | H5 solo with fills, qty, fees, funding, exchange floor | Solo only. Risk compounds from $8 (not fixed $8). No OLD3 interaction |

No completed OLD3+H5 shared-account study was found. Reuse plan:
- Codex's `overlay_sim.py` admission/halt/event-order logic is the base. It is extended only to (a) executable quantities and (b) mixed 1h/4h tapes.
- The H5 tape uses the exact `run_case.py` engine settings and its exchange-rules snapshot.
- The solo runs are not redone as separate studies. The two "alone" books are re-derived from the same tapes in the same replay, so all three books are matched.

## 2. Sleeves (no parameter changes)
| Sleeve | Strategy key | Side mask | TF | Mgmt (`S.merge_mgmt`, master 7b5beecb) | Per-sleeve capacity |
|---|---|---|---|---|---|
| OLD3-BB | `bear_breakdown` | short | 4h | stop 2 ATR, trail 3 ATR | 2 **[C]** |
| OLD3-EMA | `ema_st` | long | 4h | stop 2.5 ATR (plus signal exits) | 2 **[C]** |
| OLD3-HOT | `hot_coin` | long | 4h | stop 2 ATR, trail 2.5 ATR, tp1 50% at 2R | 2 **[C]** |
| H5 | `h5_volume_impulse_long_1h` (`h5_hourly_long.RESEARCH`, PR #338 head 5c95508f, module sha 8677d4af…) | long | 1h | stop 3 ATR, TP 4R, 48 bars | 1 |

- **[C]** Codex pins the OLD3 sides, max_pos and any mgmt overrides against the account-2 settings snapshot (read-only). The table uses the account-2 definition used in the PR #339 overlay.
- H5 is registered into a local copy of `S.STRATEGIES` inside the runner only. No repo or runtime registration.

## 3. Data and window
- One source for both TFs: canonical `cohort9_1h` (F:/ZackBot2_data/C1H-NATIVE-DATA-20261112/inputs/cohort9_1h). This is the same input as the H5 canary rerun. **[C]** Pin its manifest (c11ed256…?).
- Coins: BTC ETH XRP SOL DOGE BNB ADA LINK AVAX.
- 4h bars are built from canonical 1h: epoch-aligned [t, t+4h), only complete 4-bar groups, o/h/l/c/v/qv from the 1h rows.
  - This differs from the 15m-derived tapes in #339 at the 4 known disagreement hours. That will be disclosed.
- Warmup is 220 bars per TF, as `backtest.run`. Common evaluation start = the later of the two warmup ends (the 4h one, about 37 days). End = last complete common bar.
- H5 lots before the common start are dropped from every book. H5-alone is reconciled to the 220-close canary count.
- Exchange rules: the `run_case.py` snapshot (sha d1ef2a9c…). **[C]** Give its path.

## 4. Candidate tapes (frozen before the replay)
1. Run `backtest.run` per sleeve standalone with `max_pos=9` (one lot per coin), `daily_halt=1.0`, no risk_rules, no Kelly and large start equity.
   - Sizing does not affect prices or exits, so each lot's price path is independent of quantity.
   - `fill_log` records every fill's reference price (pre-slip) and its quantity fraction (entry, tp1 partial, final exit).
   - Two candle paths: `path` and `worst`.
2. Replay quantity per admitted lot, with fixed $8 entry risk (not compounding):
   - qty = floor(min(8 / stop_dist, notional_cap / entry_fill) / step) × step.
   - A lot below minQty or minNotional is skipped and counted.
   - The tp1 partial uses floor(qty × 0.5 / step) × step, and the remainder exits at the final fill.
   - notional_cap: **[C]** $3,333.33 as in earlier harnesses, or the account-2 live cap.
3. Fills and costs:
   - Every fill has 2 bp adverse slip on its reference price.
   - Fee is (c − 2) bp of fill notional, for c = 7/15/23 bp per executed side (the H5 convention).
   - Funding proxy: 1 bp/8h, charged on both sides each held hour at qty × that hour's canonical 1h close.
   - These are assumptions, not native funding.
4. Identity checks before the replay:
   - The tape P&L recomputed at tape quantity equals the `backtest.run` trade P&L (tolerance 1e-8 relative).
   - Every lot's exit is after its entry.
   - No duplicate ids.

## 5. Shared-account replay (one deterministic event loop)
- **Capital:** $4,000. Equity = closed cash. Marks use hourly canonical closes.
- **Event order at the same timestamp:** exits (and partials) first. Then entries in tie order: sleeve rank, then coin rank, then lot id.
- **Admission, in this order (first failing reason recorded):**
  1. Cairo-day halt active.
  2. Sleeve open lots ≥ capacity.
  3. Own sleeve already holds this coin.
  4. Open entry risk + 8 > 2.5% × closed equity.
  5. Exchange floor.
- **Phantom occupancy:** a refused candidate still occupies its own sleeve and coin until its tape exit. It carries no risk and no cash.
  - Why: the standalone tape had that coin busy, so signals the sleeve would have seen if the slot had been free are not on the tape.
  - Phantom-blocked hours are reported. This is a known conservative bias, not a model of live behaviour.
- **Same coin/side across sleeves:**
  - Lots stack as separate engine lots, which is the live default (duplicate policies OFF, PR #339 unmerged).
  - Opposite sides coexist (the engine runs in hedge mode).
  - Each lot keeps its own stop, partial and exit.
  - Max stacked coin/side risk is reported.
  - No refuse or cap policy is applied in this study.
- **Cairo-day 3% net-cash halt:** realized net P&L (fees and funding included) since the Cairo day start ≤ −3% of day-start closed equity blocks new entries until the next Cairo day. Open lots continue.
- **Open risk:** entry risk counts until the lot's final exit. A partial does not release it (conservative).
- **10% drawdown:** the first crossing of marked DD ≥ 10% is reported only. It is never enforced and never used for selection.

## 6. Books and tie sensitivity
- Three books from the same tapes and the same replay code:
  - OLD3 alone (3 sleeves);
  - H5 alone (1 sleeve);
  - Combined (4 sleeves).
- Primary order (seed 0): sleeves BB, EMA, HOT, H5; coins alphabetical.
- Sensitivity: seeds 1-39 independently shuffle the sleeve and coin ranks (`random.Random(seed)`). The same order is applied to all three books.
- **Cost × path cells:** 3 costs × 2 paths × 40 orders × 3 books = 720 replays.

## 7. Report (no gates on activation; all outcomes retained)
**Per book and cell:**
- net $ and % after costs and funding proxy;
- closes;
- executed entries per week;
- marked DD and closed DD, and the date of the 10% crossing;
- blocked entries by reason, and phantom-blocked hours;
- concentration: P&L share of the top coin and top sleeve, and the result with the single best lot removed;
- early/later split at 2025-10-01 entry, plus chronological halves.

**Incremental effect, per order:**
- Combined minus OLD3-alone, and combined minus H5-alone, in percentage points.
- Report the median, p10 and p90 across the 40 orders, and wins out of 40.

**Proposed descriptive reading [C] (not an activation gate):**
- The basket "adds value" only if, at 15 bp, both paths have combined > each alone in ≥ 30/40 orders.
- Combined 15 bp net > 0 in both the early and later halves.
- Combined 23 bp net > 0.
- High DD is reported, not an automatic rejection.

## 8. Freeze and tests before any count or outcome
- Freeze locally under `research/basket_old3_h5/` (not research_evidence): contract JSON, tape generator, replay, data and rules hashes, and one trial record.
- Synthetic tests:
  - qty floor, minQty/minNotional skip and notional cap;
  - tp1 partial floor and remainder;
  - slip and fee per fill;
  - funding per held hour;
  - exit-before-entry at the same timestamp;
  - sleeve cap and own-coin slot;
  - phantom occupancy;
  - 2.5% open-risk refusal;
  - Cairo-day halt across midnight (Cairo DST) on realized net only;
  - partial not releasing risk;
  - stacking and opposite-side coexistence;
  - deterministic orders (seed 0 alphabetical);
  - the tape identity against `backtest.run` P&L;
  - 4h build dropping incomplete groups.
- Mutation check on admission and qty lines. CP1252 shim.

Not in scope: new signals, grids, coins or windows; the 8-coin long window; refuse/cap policies; account, runtime, key or engine changes.
