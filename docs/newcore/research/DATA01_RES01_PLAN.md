# DATA-01 / RES-01 scoping plan r3 (Claude Code, 09 Oct 2026 Cairo; applies ruling 6070934398 + clarifications 6071140643)

Scope: owner alignment on issue 13 (comment 6070651161) makes strategy evidence the primary lane once the three safety
blockers (two S1 P1s, #47 target-orphan) close. This plan is the short, buildable cut of the full contract draft
`docs/newcore/research/DATA01_RES01_DRAFT.md` on `prep/data01-res01` (ed778f3); schema detail lives there. **No new
framework:** everything below extends code that already exists on master or on the `nc-*` branches. R4-R7 claim no
evidence until the rules below are executable in R1-R3.

## 1. Frozen point-in-time dataset (DATA-01 v1)

| Item | Decision |
|---|---|
| Venue / symbols | Binance USD-M USDT perps. Universe `pit-top40-qv30d-v3` (section 1a), ranked per asset-class book (`crypto`, `gold-commodity`, `equity`, `fx`); XAUUSDT stays in the research in the `gold-commodity` book (owner decision). Core 8 (BTC ETH SOL BNB XRP DOGE AVAX LINK) reported separately as the legacy comparison. |
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
  quote volume. Two **independent** PIT eligibility tests, both required: (a) listing age >= 30 days at the ranking
  instant; (b) the strategy's own warm-up (its lookback in closed bars) is fully available before its first decision.
  Neither substitutes for the other.
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
  `slip_bps = max(2, c x 10^4 x TR-SMA14 / close)` on the signal timeframe's closed bars (TR-SMA14 = the simple mean
  of the last 14 true ranges, not Wilder's ATR; Wilder ATR14 is the `slip_wilder_atr14` sensitivity row). Gap/stop fills at
  the bar open when price opens through the stop. Unspecified `k x ATR proxy` is not accepted.
- **Calibration config `slip-cal-v1` (fully frozen, hashed, cited by digest):** names the observed target (per-side
  fill-vs-reference bps), the source series, the estimator and loss, symbol pooling, the fallback when execution-grade
  observations are absent, and **one** deterministic primary limit-touch rule (touch = no fill; price-through = maker
  fill). Fitted once on its calibration interval, which is **excluded from all evaluated evidence**; never refitted per
  candidate or per holdout. No-fill and taker treatments of a limit target are **named stress rows**, never a
  post-result analyst choice.
- **Filters:** from the rules snapshot valid at decision time; a size that rounds below minimum is a counted refusal
  (reuse `exchange_rules.py` / `feasibility.py`); `RULES-BACKFILLED` periods per section 1a.
- **Stress rows (always reported):** fees+slippage x2, funding x2 (sign kept), slippage x5 on gap/stop fills,
  `limit-no-fill`, `limit-as-taker`, flat-funding legacy row.

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

- **Finite ex-ante horizon:** every prereg freezes a hard maximum holding / time-exit horizon. A candidate with an
  uncapped runner cannot open a sealed split.
- **Immutable UTC splits:** every prereg names fixed UTC train, walk-forward and holdout boundaries (timestamps, not
  "last 12 months"). Each boundary is purged by at least `max(strategy lookback, declared max holding horizon)` - the
  **declared cap, never realised durations** - and embargoed by one decision interval; an episode belongs wholly to one
  split.
- **Preregistration (`zb-prereg/1`, committed before any run):** `candidate_id`, hypothesis family id, mechanism (why,
  which side, which regime), universe rule, manifest digest, timeframe, primary parameter set, neighbour grid (+/- one
  step), primary endpoint (episode-clustered mean net R), baselines and the paired p-value definition, rejection
  criteria (EDGE-00 rows), the split boundaries, slippage calibration sample, stop condition, author, Cairo date.
- **Atomic family-wide holdout reveal:** the first sealed-holdout access is one preregistered atomic evaluation of the
  candidate plus every declared baseline and stress row. Its completion spends that window for every descendant,
  rename or variant of the economic family. Only a deterministic rerun of the identical run digest may reproduce it
  (no code / config / seed change, no new decision). The harness refuses a holdout run without a prior committed
  prereg matching digest/parameters. Ledger kinds `holdout_reveal` / `holdout_rerun` enforce this (R1 status below).
- **Hypothesis-family contamination ledger:** append-only `ledger/<family>.jsonl` records every data access, variant,
  grid point and baseline choice per **economic-hypothesis family** (not only per candidate/prereg id), with the
  window touched. `n_trials` and the Holm family are read from it. **CI check:** the ledger file's Git history must be
  append-only (every commit's version is a prefix of its successor along the ancestry); a rewrite fails CI.
- **Spent windows:** all three legacy-derived families have seen the **whole legacy window (2021-12-19 -> 2026-10-04)**,
  not only 2025-26. It is development evidence only; an untouched claim needs post-freeze forward data; the rejected
  short family is not retuned against it. Each is pre-entered as `window_spent` in
  `research_evidence/ledger/<family>.jsonl` with its evidence.

### 4a. Contamination inventory (grep of legacy code, results and issue 13; 09 Oct 2026)

| Family | Evidence the 2025-26 data was already viewed |
|---|---|
| `range_bb_mr` (`RANGE-BB-MR.v1`) | Issue 13 comment 6056630665 (M4, 08 Oct): in-sample 2022-24 -0.110R **and holdout 2025-26 +0.009R** computed; mirrored short run on both. Comment 6056676612 (Codex): BB-family 2025-26 holdout spent. The legacy `strategies.regime()` range flag (`strategies.py:243-283`, 4h ADX + BB-width percentile) gates `grid.py` (`need_range`, lines 21/82/308) over the same data. Its prereg `range_research/PREREG.md` was never committed. **No historical holdout is untouched for this family.** |
| `trend_ema_mom` | `research/long_all.csv` rows 13-14 (`strat:ema_mom` 4h from 2022-01-25, 1h from 2022-09-04, to 2026-10-04, y2025/y2026 columns); `research/results_single.csv` (`data/` 2024-09-14 -> 2026-10-04); `research_long2.py` `bull()` regime gating; M3 pack. |
| `short_breakdown` | `research/long_all.csv` rows 25-26, 35, 45, 55 (`bear_breakdown`, 2022-01-25 -> 2026-10-04); `research/results_single.csv`. |

## 5. EDGE-00 gate (promote only if every row passes; else PARK/REJECT as stated)

| Row | Rule |
|---|---|
| Sample | >= **60** independent episodes total, >= **30** sealed-holdout episodes, >= **6** non-overlapping eligible calendar-month blocks; else PARK. |
| Expectancy | **Sealed-holdout-only** lower 95% episode-clustered CI of net R > 0; walk-forward mean > 0 checked separately, never pooled to rescue the holdout. |
| Baselines | Pairing units predeclared: **episode-paired** where matched entries permit it (B0); **same non-overlapping calendar blocks, equal-risk returns** for continuous hold (B1/B2); cash (B3) reduces to the expectancy test. Pass = Holm-adjusted paired p-value < 0.05 per baseline with ordinary effect CIs reported, or explicitly inverted simultaneous CIs. An ordinary interval is never called "Holm-corrected". Not raw mean/median comparisons. |
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

## R1 status (source + fixture preparation only; no sealed or evidence run)

- `tools/research/manifest.py`: `zb-data-manifest/1` build / validate / verify / write-once, canonical-JSON SHA-256
  digest. Committed `research_evidence/manifests/legacy-unverified-v1.json` (64 files = `DATA_MANIFEST.json`, digest `0cc8ba49...`).
  Paths are canonical forward-slash paths relative to a logical `data_root` (`repo`), resolved (symlinks/junctions
  included) and proven inside the allowed store before any read (Codex R1 P2).
- `tools/research/ledger.py`: `zb-ledger/1` hash-chained JSONL, append-only byte check, Git-ancestry check, holdout
  reveal / rerun rules, `n_trials`. A `holdout_rerun` must repeat the reveal's full identity: family, candidate/prereg
  id, manifest digest, window, `detail.eval_digest` (evaluation code + config + seeds) and `run_digest` (Codex R1 P1).
  Reruns are capped (`MAX_RERUNS` = 2, or a lower `detail.max_reruns` declared in the reveal). Holdout-split
  data_access / variant / grid_point / baseline records must belong to the atomic reveal (directly after it, same
  identity); no variant / grid_point on a revealed window afterwards. `research_evidence/ledger/REGISTRY.jsonl` is the
  cross-file registry: every family is declared once with its lineage (`parent`, null = independent root) and genesis,
  and every spend is copied there, so a renamed or descendant family cannot reveal a window spent in its lineage.
  `verify()` / `ledger.py check` is the single check and always runs records + registry + Git history (+ the CI base);
  a ledger file with no parent version must be its declared genesis (registry pin `REGISTRY_GENESIS`). Strict dates,
  no bool-as-int, no NaN, strictly increasing bars, and an append lock (Cowork 6072246284).
  R3 hook: `recompute_run_digest()` will recompute `run_digest` from the frozen run envelope. Tests:
  `tests/test_res01_manifest_ledger.py`.
- **Data gaps (nothing downloaded).** Local data is klines only (`t,o,h,l,c,v`, survivor-only;
  `data/exchange_rules_testnet.json` is a testnet snapshot). Missing, all public, each archive with a `.CHECKSUM`:
  1. All-listed incl. delisted USD-M symbols + onboard / delivery dates: the `data.binance.vision` listing of
     `data/futures/um/monthly/klines/` (historically listed symbols) plus `fapi/v1/exchangeInfo`
     `onboardDate` / `deliveryDate` / `status` for the current set.
  2. Timestamped delist announcements: Binance announcement "Delisting" category (no archive API; record fetch time).
  3. Mark-price klines: `data/futures/um/monthly/markPriceKlines/`.
  4. Signed funding: `data/futures/um/monthly/fundingRate/` (+ `fapi/v1/fundingRate` tail).
  5. 1m klines for intrabar resolution: `data/futures/um/monthly/klines/<SYMBOL>/1m/`.
  6. Historical rules snapshots: no public history before our own first snapshot, hence `RULES-BACKFILLED`.

## R1 data + R2 status (09 Oct 2026 Cairo; data and universe only, no strategy run, no returns computed)

- Archive store (outside the repo, logical `data_root` `binance_um`): `data.binance.vision` UM monthly zips for all 900
  historically listed USDT perps (klines 1d + 4h, markPriceKlines 1h, fundingRate) plus klines 1m for the 432-symbol
  top-40 union. `research_evidence/manifests/binance-um-archive-v1.json.gz` (`zb-binance-vision-zip/1` loader):
  103,659 files, digest `54912d9d...`. Every zip re-hashed against its published checksum and schema-checked row by row
  (0 failures). Gzip is a byte-exact wrapper of the canonical JSON (62 MB plain).
- `tools/research/universe.py` -> `research_evidence/universe/pit-top40-qv30d-v3.json` (`zb-pit-universe/2`,
  Codex reviews 6077570517 + 6078823692 applied; digest `fd6e15d0...`): 348 Monday rankings 2020-02-03 -> 2026-09-28 over 900 symbols, ranked separately
  per book from `research_evidence/universe/instrument-classes-v1.json` (`zb-instrument-classes/1`; offline manual
  review, every post-2025-12-01 listing has an explicit entry, 27 unidentified symbols are `unclassified` and join no
  book). Exact decimal volume sums; any missing daily bar in a scored window vetoes `data-gap` (never zero volume) and
  every symbol's internal 1d gaps are listed (`missing_days`). Renames fail closed. Every week `RULES-BACKFILLED`.
  The v1 artifact (one mixed list, float sums, silent zero-volume gaps) is withdrawn and must not be used.
  Codex 6078823692: ranking is a comparison-only exact sort (no negated Decimal key rounding to 28 digits), and a
  listed contract with no bar in the whole window and no delist observation is vetoed `data-gap:window-absent` with a
  30-day `gaps` entry instead of vanishing. v3 has identical book memberships to v2 in all 348 weeks and adds 4,629
  window-absent audit vetoes (max 31 per week); v2 is withdrawn (universe files are immutable, hence the new id).
- Provenance, stated truthfully: the store keeps each zip plus a `.ok` sidecar holding the normalized 64-hex SHA-256
  parsed from the published `.CHECKSUM`; the raw `.CHECKSUM` bytes are **not** preserved, and the manifest carries no
  `zb-data-gaps/1` gap-report digest yet. The only bound gap report is the 1d one inside the universe artifact.
  Section 1's raw-checksum and gap-digest items stay open (checklist below).
- Open: timestamped delist announcements; rename evidence + price-continuity check (renames refused until then);
  an exchangeInfo `underlyingType` snapshot to replace the manual classification; 1m archives for the 11 post-2021-12
  universe members outside the 1m union; raw `.CHECKSUM` preservation + manifest gap-report digest.
- Open (owner direction 6078694212, awaiting a Codex ruling, not applied): a labelled `not-addressable: non-ASCII
  symbol` eligibility veto applied before ranking and listed in the weekly audit. Sourced point-in-time exchange
  classification still gates strategy evidence (Codex 6078823692).

## Ruling 6070934398 applied

- [x] 1 Immutable UTC splits, purge/embargo, one split per episode -> section 4
- [x] 1 Sealed-holdout-only CI, walk-forward separate and never pooled -> section 5 (Expectancy)
- [x] 1 Hypothesis-family ledger + CI Git ancestry check -> section 4, R3
- [x] 1 Spent 2025-26 window for `trend_ema_mom` and short family -> sections 4, 7
- [x] 2 All-historically-listed universe, Monday 00:00 UTC ranking, age + warm-up -> section 1a
- [ ] 2 Renames: refused (fail closed) until time-scoped identity + price-continuity evidence exists
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
- [x] 5 Archive primary after verification, object/hash/loader version, REST tail-only -> section 1
- [ ] 5 Raw `.CHECKSUM` bytes preserved + gap-report digest on every manifest (not yet: `.ok` holds the normalized
  hash; only the universe's 1d gap report is bound)
- [x] 5 Signed funding at timestamp, closed-bar availability, period semantics recorded -> section 1
- [x] Verdict: R1-R3 first; R4-R7 claim no evidence until executable -> header, section 8

## Clarifications 6071140643 applied

- [x] 1 Atomic family-wide holdout reveal, deterministic rerun only; `RANGE-BB-MR` inventory -> sections 4, 4a; ledger kinds
- [x] 2 Finite ex-ante horizon; purge uses the declared cap -> section 4
- [x] 3 Fully frozen, hashed slippage calibration; no-fill / taker as named stress rows -> section 2
- [x] 4 Pairing units, Holm wording, non-overlapping month blocks -> section 5
- [x] Listing age and warm-up are two independent PIT tests -> section 1a

## Cowork plan review 6071450810 folded in (binding on the prereg / R2-R3; no R1 code change)

- [x] 1 Delist exit: next bar open with `available_ms` >= timestamped notice; no observed notice = last archived mark
  with 5x slippage; forced-exit PnL reported separately -> section 1a
- [x] 2 Trend/short holdout is forward-only: any run before ~30 sealed forward episodes is development evidence;
  forward manifests extend the frozen one and cite the parent digest -> sections 4, 7
- [x] 3 B0/B1/B2 matching keys (symbol, side, regime, hold horizon, count), identical costs/funding/universe and the
  equal-risk scaling are frozen in the prereg before any reveal -> sections 4, 5
- [x] 4 Holm: one family = every ledger variant of the hypothesis family, block = calendar month; report family size
  and the minimum detectable effect -> section 5
- [x] 5 1000x re-denominations / contract-type changes are new instruments; renames join only after a price-continuity
  check -> section 1a
- [x] 6 PIT rules: funding at T counts for a position open over T (entry == T included, exit == T excluded); a higher-TF
  bar is usable only after its last sub-bar closes; joins on `available_ms`, no forward-fill across a gap > 1 bar -> section 1
- [x] 7 Regime/range filters use trailing or expanding data only; first signal at warm-up + 1; EMA seeded from the first
  bar -> section 3
- [x] 8 Prereg names walk-forward fold boundaries/count and the slippage-calibration interval; all folds reported -> sections 2, 4
- [x] 9 R3 exit tests: future-perturbation, negative-control peeking strategy, Cowork canary suite (noise ~ -round-trip
  cost, funding sign, qty round-up), fixed float-sum order + per-shard seeds, library versions + dirty tree invalidate a
  run, SHA re-verify at load fails closed -> section 3, R3
- [x] 10 Quantity floored to step, prices rounded adversely, never silently upsized -> section 2
- [x] 11 This plan's thresholds are authoritative (60 episodes / 80% neighbours); add a per-cell floor and >= 10k
  bootstrap resamples -> section 5
- [x] 12 Missing 1m bars = unresolved ambiguous bar, counted toward the 5% PARK rule -> section 2a

## R3 status (09 Oct 2026 Cairo; harness core only, no strategy evaluated, no returns computed)

Codex R3 review 6077894871 applied (stacked on the fixed R2 universe `pit-top40-qv30d-v3`):

- `tools/research/pit.py` (the plan's `data.py`): `Dataset` reads only through the manifest (SHA-256 re-check, fail
  closed) and the PIT universe, restricted to named asset-class books (no mixed default; gold from `gold-commodity`).
  `Access` records every opening as a ledger `data_access` before returning data. Capability boundary: evaluator code
  receives only the frozen `View` (an opaque key, no Dataset/Window reference) through `evaluate()`, which re-runs every
  decision with all not-yet-available rows perturbed and fails closed if the output changes (the peeking negative
  control is caught). The caller-supplied `entered_ms` is gone: a symbol outside the universe is served only under a
  `Position` capability issued by `View.enter()` from a recorded decision (unforgeable, not back-datable, per window).
  Funding joins use the actual funding timestamps (no assumed cadence) and the funding-mark proxy v1 (1h mark close
  ending at or before T), never forward-filled across a gap. The sealed holdout opens only when the stored envelope's
  code identity is re-proven on the executing checkout (`report.verify_code`) and the ledger's open atomic group carries
  this run's complete identity.
- `costs.py` (`zb-cost-model/2`): explicit symbol -> cost class mapping from the universe classification, no crypto
  fallback. Rows: `crypto` (PLAN); `gold` (XAUUSDT: Binance VIP0 fees, its own observed 4h funding cadence checked
  against the rows, its own slip `c`, weekend reference-gap label; PROVISIONAL); `commodity`, `equity`, `fx`
  (classified, UNCALIBRATED). `slip-v1` uses `TR-SMA14`; Wilder ATR14 is a sensitivity stress row; `slip-cal-v1` has
  `fitted` (false = labelled `SLIP-C-UNFITTED`). Nine stress rows incl. `slip_wilder_atr14` and `funding_mark_adverse`
  (the v1 mark proxy stressed 50 bps against the position; compare with exchange-reported funding in forward/testnet
  records).
- `report.py` (`zb-research-run/2`): `freeze_run` captures HEAD, clean-tree status (outside `research_evidence/`),
  Python and the canonical dependency set itself, hashes the canonical evaluation file list (`CORE_EVAL_FILES` + the
  candidate's tracked files) with config and seeds into `eval_digest`; `verify_code` re-captures and recomputes all of
  it at holdout access (moved HEAD, dirty tree, changed file, other Python/deps fail closed).
- `ledger.py`: every holdout record (reveal, rerun, component) needs the immutable runs store and its recomputed
  envelope; a scratch ledger is development-only. Components must carry the reveal's complete identity (candidate
  included); a rerun opens its own access group after full identity binding.
- `splits.py`: immutable UTC `SplitPlan`; purge = max(lookback, declared horizon), embargo = 1 bar; decisions in
  `[start + purge + embargo, end - horizon]`, so an episode and its look-back stay in one split; one holdout, last.
- `intrabar.py`: 1m resolution; same-minute and missing/incomplete 1m fall back to stop-first, labelled and counted
  as unresolved toward the 5% PARK rule; both bounds kept.
- Report shape per section 6 x every stress row; envelopes write-once under `research_evidence/runs/`.
- Smoke (shape/coverage only, development split, `research_evidence/ledger/res01_infra.jsonl`, run on the v1
  universe before the #51 fixes; not re-run): 2026-01-05..01-12
  (BTC, ETH; XAU not yet a member) and 2026-02-02..02-09 (XAU, BTC): full 1m/4h/1d/mark coverage; XAUUSDT funds every
  4h (42/week) vs 8h for BTC.

Codex R3 fix review 6079042573 applied (merged with the fixed R2 head; universe `pit-top40-qv30d-v3`):

- Evaluator isolation: `pit.evaluate(window, evaluator_path, function, times)` runs the evaluator in a separate
  process (`tools/research/sandbox.py`, `zb-eval-sandbox/1`) that holds no Dataset, Window, manifest, store path or
  ledger. Its View is a proxy answered by the harness from the View frozen at the decision time; an audit hook refuses
  reading anything but Python's library and `.py` code outside `research_evidence/`, directory listings elsewhere,
  writes, process creation, sockets and ctypes. This is a runtime control, not claimed as a security boundary; private
  Python names are explicitly not one. The perturbation re-run is kept.
- Runner authority: `evaluate` appends a `zb-eval-attestation/1` (runner, isolation, perturbation, split/window,
  run_digest, evaluator file hash, digests of times / outputs / recorded decisions) to the family ledger;
  `report.make_report(env, results, attestation, ledger)` refuses an attestation that is unperturbed, not sandboxed,
  for another run/split/window, whose evaluator file is not in the envelope's hashed evaluation files, or that has no
  matching ledger record. Results from `Window.view` directly or `perturb=False` cannot be sealed.
- Holdout ledger: the holdout opens only from the canonical registered ledger and runs store
  (`research_evidence/ledger` + `research_evidence/runs` of the canonical research checkout, which must be the access
  repo) and only after `ledger.verify` (registry genesis pin + append-only Git history) passes at access. A scratch
  ledger/runs tree, or a freshly minted registry at the canonical path, is refused.
- Code identity: "dirty" ignores only non-executable evidence artifacts (`.json`, `.jsonl`, `.json.gz`, `.csv`, `.md`
  under `research_evidence/`); a `.py` (or any other file) there is code. The evaluation file list is closed over static
  imports (`import_closure`), and evaluation code in or imported from `research_evidence/` is refused at freeze.
- Funding continuity: `check_funding_cadence(symbol, rows, start_ms=, end_ms=)` requires consecutive events exactly one
  declared interval apart (+/- 60 s; a schedule switch accepted at the switch), no missing event before the first or
  after the last row of the covered interval, and refuses an empty span longer than one interval. No forward fill.
- Gold: cost classes `gold-spot` (XAUUSDT, 4h, PROVISIONAL) and `gold-tokenized` (PAXGUSDT / XAUTUSDT, per-symbol
  cadence, UNCALIBRATED), both in the `gold` regime family (`CostModel.regime_family`); never the commodity fallback.
- Open: the non-ASCII `not-addressable` universe veto awaits a Codex ruling (owner direction 6078694212).
