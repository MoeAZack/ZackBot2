# DATA-01 / RES-01 scoping plan r2 (Claude Code, 09 Oct 2026 Cairo; applies Codex ruling 6070934398)

Scope: owner alignment on issue 13 (comment 6070651161) makes strategy evidence the primary lane once the three safety
blockers (two S1 P1s, #47 target-orphan) close. This plan is the short, buildable cut of the full contract draft
`docs/newcore/research/DATA01_RES01_DRAFT.md` on `prep/data01-res01` (ed778f3); schema detail lives there. **No new
framework:** everything below extends code that already exists on master or on the `nc-*` branches. R4-R7 claim no
evidence until the rules below are executable in R1-R3.

## 1. Frozen point-in-time dataset (DATA-01 v1)

| Item | Decision |
|---|---|
| Venue / symbols | Binance USD-M USDT perps. Universe `pit-top40-qv30d-v1` (section 1a). Core 8 (BTC ETH SOL BNB XRP DOGE AVAX LINK) reported separately as the legacy comparison. |
| Timeframes | 4h and 1h (trend, short, range). 1m for intrabar resolution of every candidate (section 2a); 15m + 1m for Quick Bank (DATA-01a). |
| Series | last-price klines, mark klines, signed funding history (`fundingTime`), rules snapshots. OI / long-short / taker only if a preregistration needs them. |
| Sources | `data.binance.vision` archives are primary **only after first-build schema/coverage verification**. Raw archive bytes and the published `.CHECKSUM` bytes are preserved; checksums verified; exact object key/hash and loader version recorded. REST (`market_data.py`, `market_collector.py`) is **tail-only**, overlap cross-checked (gap class X1); no silent substitution. |
| Window / freeze | 2021-12-19 -> freeze date (first build). Everything after the freeze date is forward-only data. |
| Point-in-time rule | every row carries `available_ms` (closed bar: open + interval; funding signed at `fundingTime`, period semantics recorded); the harness view filters on `available_ms <= t` only. |
| Manifest | `zb-data-manifest/1`: per file source URL/object key, archive checksum, our SHA-256, loader version, rows, first/last, `available_ms` rule, Cairo access time, gap-report digest; manifest digest = SHA-256 of canonical JSON. A result cites one manifest digest; manifests are never edited. |
| Gaps | `tools/research/gap_check.py` (`prep/data01-gapcheck`, 4f3cb49) emits `zb-data-gaps/1` on every manifest; report committed. |
| Legacy | `data_long/`, `data/`, `data1h/`, `DATA_MANIFEST.json` import as one `legacy-unverified` survivor-only manifest, used only to reproduce M3. All `research/*` outputs retired as evidence. |

### 1a. PIT universe and exchange rules

- Eligibility comes from **all historically listed** USD-M USDT perpetuals, including delisted contracts (archive
  listing), never current `exchangeInfo`. Contract identity is preserved across renames.
- Weekly ranking at **Monday 00:00 UTC** using only rows with `available_ms` <= that instant: top 40 by trailing 30-day
  quote volume, listing age >= 30 days **plus the strategy's warm-up**.
- Delist veto starts only at a **timestamped** announcement/status observation available at that time. Exit uses the
  settlement or last reliable mark plus stress; never future delist knowledge.
- Rules (tick, step, min qty, min notional) before the first genuinely observed snapshot are labelled
  **`RULES-BACKFILLED`**: reported with sensitivity bands, never used to gate refusal/feasibility or EDGE E11. Exact
  operational claims start only where timestamped rule snapshots exist.

## 2. Cost, funding, slippage and filter model

- **Fees:** VIP0 taker 0.05% per side for market entries/exits. Maker 0.02% only for a resting limit target with
  **price-through or ordered-trade evidence** of the fill; a mere touch is modelled as no-fill or taker (conservative)
  and stressed. No maker entries in v1. Tier recorded in the run.
- **Funding:** signed actual rate x mark notional at each `fundingTime` the position spans (longs pay positive, shorts
  receive). The legacy flat 0.005%/bar stays only as a reproduction row.
- **Slippage (frozen formula, `slip-v1`):** per side, in bps of fill price,
  `slip_bps = max(2, c x 10^4 x ATR14 / close)` on the signal timeframe's closed bars (lookback 14), coefficient `c`
  fitted once on a PIT calibration sample fixed in the prereg (first 6 months of the manifest, before any split it is
  evaluated on) and then frozen in the manifest-linked config. Gap/stop fills at the bar open when price opens through
  the stop. Unspecified `k x ATR proxy` is not accepted.
- **Filters:** from the rules snapshot valid at decision time; a size that rounds below minimum is a counted refusal
  (reuse `exchange_rules.py` / `feasibility.py`); `RULES-BACKFILLED` periods per section 1a.
- **Stress rows (always reported):** fees+slippage x2, funding x2 (sign kept), slippage x5 on gap/stop fills, limit
  target as taker, flat-funding legacy row.

### 2a. Intrabar resolution

- Always report **stop-first and target-first** results plus the ambiguity rate (both touched in one bar).
- Ambiguous bars are resolved with 1m data; if both touch inside one minute, use ordered trades when available,
  otherwise stop-first. Stop-first alone is only the conservative fallback bound.
- **PARK** if unresolved ambiguity exceeds **5% of trades** or flipping it changes any gate verdict.
- Quick Bank / scalp always requires 1m plus spread/latency evidence.

## 3. One shared causal harness (RES-01)

Built inside the existing NEWCORE research path, not beside it:

- **Strategy input:** the NEWCORE strategy `Evaluation` (`newcore/strategy/*`, `nc-strategy-ema-mom`), pure,
  closed-candle, called with a PIT view `Dataset.view(t)`. Each candidate is one adapter: `evaluate(view, symbol, side)
  -> Signal | None` plus a management preset.
- **Execution + management:** the S4 Runner path (`newcore/runner/*`, FakeVenue/FileJournal on `nc-s1-slice`) and the
  pure NC-07 core (`newcore/management/{core,sim,presets}.py`). Long, short, range and scalp share fills, stop ordering
  and rounding with runtime.
- **Statistics:** generalise `newcore/runner/evidence.py` (episode-clustered bootstrap) and
  `newcore/strategy/research/ema_mom_after_cost.py`; reuse `lab.py`'s truncation lookahead check and `replay.py`'s
  parity gate as tests.
- **New modules (thin):** `data.py` (manifest load + PIT view), `costs.py`, `splits.py` (splits, purge/embargo, holdout
  guard), `ledger.py` (family ledger + ancestry check), `report.py` (`zb-research-run/1`). Research code is never
  imported by runtime (AST import test).
- **Causality tests:** truncation re-run at 200 seeded decision points reproduces every decision; entry is always the
  next bar's open; a clean-checkout run on a committed fixture manifest reproduces the run digest.

## 4. Splits, preregistration and contamination ledger

- **Immutable UTC splits:** every prereg names fixed UTC train, walk-forward and holdout boundaries (timestamps, not
  "last 12 months"). Each boundary is purged by at least `max(strategy lookback, max holding horizon)` and embargoed
  by one decision interval; an episode belongs wholly to one split.
- **Preregistration (`zb-prereg/1`, committed before any run):** `candidate_id`, hypothesis family id, mechanism (why,
  which side, which regime), universe rule, manifest digest, timeframe, primary parameter set, neighbour grid (+/- one
  step), primary endpoint (episode-clustered mean net R), baselines and the paired p-value definition, rejection
  criteria (EDGE-00 rows), the split boundaries, slippage calibration sample, stop condition, author, Cairo date.
- **Holdout guard:** the harness refuses a holdout run without a prior committed prereg matching digest/parameters, and
  refuses a second look.
- **Hypothesis-family contamination ledger:** append-only `ledger/<family>.jsonl` records every data access, variant,
  grid point and baseline choice per **economic-hypothesis family** (not only per candidate/prereg id), with the
  window touched. `n_trials` and the Holm family are read from it. **CI check:** the ledger file's Git history must be
  append-only (every commit's version is a prefix of its successor along the ancestry); a rewrite fails CI.
- **Spent windows:** `trend_ema_mom` and the legacy-derived short-breakdown family have already seen 2025-2026. For
  them that period is **development evidence only**; an untouched claim needs post-freeze forward data. The rejected
  short family is not retuned against the spent window. Both are pre-entered in the ledger with that window marked spent.

## 5. EDGE-00 gate (promote only if every row passes; else PARK/REJECT as stated)

| Row | Rule |
|---|---|
| Sample | >= **60** independent episodes total, >= **30** sealed-holdout episodes, >= **6** independent calendar-month blocks; else PARK. |
| Expectancy | **Sealed-holdout-only** lower 95% episode-clustered CI of net R > 0; walk-forward mean > 0 checked separately, never pooled to rescue the holdout. |
| Baselines | Paired candidate-minus-baseline episode distributions (B0-B3); Holm-corrected lower CI > 0 per baseline, valid paired p-values defined in the prereg. Not raw mean/median comparisons. |
| Multiplicity | Holm family = every variant, grid point and baseline choice in the family ledger. |
| PIT | The PIT-universe result itself passes the expectancy row; retaining the survivor-only sign is insufficient. |
| Robustness | Leave-one-symbol-out and leave-one-year/block-out mean > 0 (replaces net-PnL-share gates). |
| Neighbours | >= **80%** of one-at-a-time neighbours keep the endpoint sign; every evaluated configuration is in the ledger. |
| Intrabar | Section 2a: unresolved ambiguity <= 5% and no verdict flip; else PARK. |
| E10 drawdown | **PARK-only** until GOV freezes a drawdown ceiling. |
| E11 / follower | Deployability, not edge: report **100 / 200 / 500** USDT tiers; PARK only the tier that fails. `RULES-BACKFILLED` periods cannot gate it. |

## 6. Report metrics (`zb-research-run/1`, per side, per cost row)

1. After-cost expectancy: mean net R, net % equity, profit factor, win rate, avg win / avg loss; holdout and
   walk-forward shown separately.
2. Drawdown: max mark-to-market DD at bar closes; Monte Carlo (episode bootstrap, 10k) DD p5/p50/p95; longest underwater.
3. MFE / MAE per trade (R) and give-back = (MFE - realised R) / MFE.
4. Counts: trades, episodes, month blocks, refusals by reason, exposure time, ambiguity rate.
5. CIs: 95% episode-clustered bootstrap (gating) and per-trade bootstrap (reported only); seed recorded.
6. Contribution (reported, not gating): gross-positive share by symbol, year and PIT regime; top-5 episodes; LOSO/LOYO.
7. Neighbour stability table; stop-first vs target-first; stress rows; survivor-only vs PIT delta.
8. Paired baselines: B0 matched random entries (1,000 seeded), B1 hold, B2 20-bar momentum, B3 cash.
9. Follower feasibility at 100 / 200 / 500 USDT; `RULES-BACKFILLED` share.

## 7. First four candidates

| Family | Candidate | Exists | Must be built |
|---|---|---|---|
| Long trend | `trend_ema_mom.v1` (4h) | Strategy + after-cost script, S4 Runner run, M3 pack; M3 = PARK pending DATA-01 | Reproduce M3 on legacy manifest (acceptance), then PIT run; 2025-26 spent, untouched claim forward-only |
| Asymmetric short | `short_breakdown.v1` (4h), from legacy `bear_breakdown` | Legacy signal in `strategies.py`; `runner/mirror.py` | NEWCORE adapter with own calibration; no retune on spent 2025-26; forward evidence for untouched claim |
| Range / MR | `RANGE-BB-MR.v1` (4h) | Preset in `newcore/management/presets.py`; legacy `regime()` filter | Entry adapter + PIT regime label |
| Quick Bank scalp | `quick_bank.v1` (15m, 1m intrabar) | NC-07 TP1 / BE / runner / bounded DCA | Entry adapter, DATA-01a slice, spread/latency stress |

## 8. Slice order (one PR each, READY FOR CODEX + HANDOFF TO COWORK per slice)

| # | Slice | Rough size |
|---|---|---|
| R0 | Gap checker merge + this ruling | review only |
| R1 | DATA-01 loader: archive fetch + raw/checksum preservation, schema/coverage verification, manifest, fixture | ~500 LOC + tests |
| R2 | PIT universe (all-listed, Monday ranking, delist vetoes, renames), `RULES-BACKFILLED` labels, legacy import | ~300 LOC + tests |
| R3 | RES-01 core: PIT view, costs + `slip-v1`, splits/purge/embargo, holdout guard, family ledger + CI ancestry check, intrabar 1m resolver, report | ~800 LOC + tests |
| R4 | Acceptance: M3 on legacy manifest, then first PIT `trend_ema_mom.v1` run | ~150 LOC + report |
| R5 | Short and range adapters + preregs, runs | ~300 LOC + 2 reports |
| R6 | DATA-01a + `quick_bank.v1` | ~400 LOC + report |
| R7 | EDGE-00 table across the four; recommendations for Codex | ~200 LOC + report |

## Ruling 6070934398 applied

- [x] 1 Immutable UTC splits, purge/embargo, one split per episode -> section 4
- [x] 1 Sealed-holdout-only CI, walk-forward separate and never pooled -> section 5 (Expectancy)
- [x] 1 Hypothesis-family ledger + CI Git ancestry check -> section 4, R3
- [x] 1 Spent 2025-26 window for `trend_ema_mom` and short family -> sections 4, 7
- [x] 2 All-historically-listed universe, Monday 00:00 UTC ranking, age + warm-up, renames -> section 1a
- [x] 2 Timestamped delist veto, settlement/last-mark exit + stress -> section 1a
- [x] 2 `RULES-BACKFILLED`, no gating of refusal/feasibility/E11, sensitivity bands -> sections 1a, 2, 5
- [x] 3 60 / 30 / 6 sample floor -> section 5
- [x] 3 Paired baseline CIs, defined paired p-values, Holm over full family -> sections 4, 5
- [x] 3 PIT result must pass expectancy -> section 5
- [x] 3 Leave-one-symbol/year-out replaces PnL-share gates; contributions still reported -> sections 5, 6
- [x] 3 80% neighbour stability, all configs in ledger -> section 5
- [x] 3 E10 PARK-only; follower tiers 100/200/500, PARK failing tier only -> section 5
- [x] 4 Stop-first + target-first + ambiguity rate, 1m resolution, ordered trades, 5% PARK; scalp 1m + spread/latency -> section 2a
- [x] 4 Limit-fill evidence rule (price-through/ordered trades, else no-fill/taker + stress) -> section 2
- [x] 4 Frozen slippage formula: units, lookback, coefficient, PIT calibration sample -> section 2
- [x] 5 Archive primary after verification, raw + checksum bytes, object/hash/loader version, REST tail-only -> section 1
- [x] 5 Signed funding at timestamp, closed-bar availability, period semantics recorded -> section 1
- [x] Verdict: R1-R3 first; R4-R7 claim no evidence until executable -> header, section 8
