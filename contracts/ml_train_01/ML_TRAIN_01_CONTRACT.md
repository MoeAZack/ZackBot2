# ML-TRAIN-01 — paired training ledger, then time-split baseline + calibration (shadow-only)

**Status: FROZEN — signed by Codex, 28 Oct 2026 05:50 Cairo (02:50 UTC); ruling 6459327957, accepted head `9ea2cd92`, merge `fe84dbbc`.** Contract only: no training run, and Build read no data. Codex signs it only after this
revision, a Cowork adversarial contract review and green CI. Signing sets `status` → `FROZEN`, `signed_by` and
`frozen_at` in the machine twin `ml_train_01_contract.v1.json`, which sits next to this file. Both twins live under
`contracts/ml_train_01/` rather than the documentation tree, so every edit goes through normal verification
(Codex 6453098718). They are not under `research_evidence/`: since `0944547`, new files there may only land in the
append-only `prereg/`, `ledger/` and `runs/` (the `test_bt_exec_obs01` guard), and an editable DRAFT cannot be
append-only. Built from `origin/master` @ `945f997` code only: `ml_shadow/*`, the `engine.py` ML hooks,
`actual_costs.measured/row_view`, `strategies.py` management, the installer build stamp, the AI-SCAN-01 contract
(#199/#201) and the AI-SCAN-02 ledger pattern (`origin/ai-scan-02`, `ai_scan_ledger.py`).

Binding inputs:
- Codex roadmap 6452494807;
- gates 6452143340;
- data ruling 6452728797, which fixes the order:
  **(1) paired ledger → (2) diagnostics → (3) training → (4) shadow-only authority.**
  No step starts before the one above it is accepted;
- contract review 6453098718 and blockers 6455506817, which rule on backfill, acknowledged/booked closes, model shape, the parity guard and
  five corrections.

PATH: Phase 10 ML-in-shadow → ML-SHADOW-01 ledgers (live) → **ML-PAIR-01 paired ledger** → ML-TRAIN-01 offline artifact
→ prospective shadow window → (DEFERRED) risk-reducing authority.

## 0. Why the order changed (Codex-reported figures)

These figures are **Codex-reported** from its live read-only audit (6452728797). **Build did not read live data.**

| Item | Codex-reported value |
|---|---|
| Unique decisions | 11,235 |
| Measured close labels | 308 |
| Feature-label joins that survive | **128** |
| Orphaned labels (decision rotated away) | **180** |
| Taken / pending decisions | 131 / 36 |
| Joined history | 6 decision days |
| Persisted or calibrated model artifact | none |

The cause is confirmed in code. The decision ledger is a rotating `JsonlLedger` (`engine.ML_MAX_BYTES = 2_000_000`,
`ML_MAX_FILES = 5`), and `_rotate()` deletes the oldest file. Decision rows and their features can therefore disappear
before the trade closes. At close, the writer (`ml_shadow/writer.py`, `_do_write`, label branch) finds no decision and
counts `missing_decision`. The problem is durability, not only sample size.

**Skipped opportunities are not supervised outcomes.** A `skipped` decision never receives a label. It is never
labelled as a loss or as anything else.

## 1. Paired training ledger (ML-PAIR-01) — first

### 1.1 Write point (exact code path)
`engine._measured_close` → (row still pending: `state['measured_wait']` → `engine._measured_followup`) →
`engine._measured_done` → `engine._ml_label` → `LedgerWriter.submit('label', row)` → writer thread
`LedgerWriter._do_write` (label branch). The paired row is appended there, on the writer thread, off the engine cycle
lock. It is appended **only when** `actual_costs.row_view(rec)['status'] == 'measured'` and
`actual_costs.measured(rec)['complete']` is true.

**Acknowledged and booked closes are never training pairs.** `acknowledged` (operator-typed net) and legacy `booked`
closes are counted in a separate diagnostics stratum, `non_training_closes`, keyed by basis. They are never written to
`pairs.jsonl`.

### 1.2 Keeping the decision alive until close
At decision time (`engine._ml_decision` → `submit('decision')`), a decision whose status is `taken` or `pending`
(`ledger.labelable`) is also appended to `labelable_decisions.jsonl`, a durable store that is never rotated. Skipped
decisions stay only in the rotating ledger. The pairing step reads the decision from the durable store, so rotation can no
longer orphan a label. The store's size is bounded by the number of taken and pending decisions. Pending decisions are
still recorded for diagnostics, but v1 training excludes them (§1.7).

### 1.3 Row schema `ml_shadow.pair.v1`
| Field | Content |
|---|---|
| `kind`, `schema` | `"pair"`, `"ml_shadow.pair.v1"` |
| `pair_key` | idempotency key = `(decision_id, cost_rev_id)` |
| `source` | `live` (written at a measured close) or `backfill_v1` (§1.5) |
| `decision_id`, `corr_id` | deterministic id (`ledger.decision_id`); hashed trade id |
| `decision` | the sanitized decision row as written at decision time: `ts_utc`, `candle_time`, `sleeve`, `strategy`, `family`, `symbol`, `side`, `tf`, `status`, `reason`, `rotation_group`, `features`, `missing`, `feature_hash`, `schema` |
| `label` | `closed_at`, `net_r`, `net_pnl`, `exit_reason`, `mfe_r`, `mae_r`, `target_before_stop`, `net_basis` (`measured`) |
| `costs` | from `actual_costs.measured`: `gross`, `fees_actual`, `funding_actual`, `fill_adj` (slippage vs booked fill), `risk_usd`, `complete`, `why` |
| `ids` | `code_id` (§1.6), `decision_schema`, `label_schema`, `feature_schema`, `feature_names_sha256`, `cost_model_id`, `cost_rev_id` |
| `mgmt` | immutable **effective management snapshot** (§1.7): `effective_max_bars`, `runner`, `overlay_id`, `overlay_hash`, `mgmt_hash`, `canonical_mgmt_hash`, `tf`, `horizon_h`, `mgmt_close_hash`, `mgmt_changed` |
| `epoch_hash` | `live` rows: the `epoch_hash` of `pair_epoch.json` (§1.8). `backfill_v1` rows: the `backfill_epoch_hash` of `backfill_epoch.json` (§1.5) |
| `provenance` | backfill only: `source_files` (sha256 of every source ledger and history file read), `reconstructed_by` (code id) |
| `paired_at` | UTC ISO time of the append. **Metadata only: it is excluded from `record_hash` and from every training identity** (§1.4) |
| `record_hash` | sha256 of the canonical row **without `record_hash` and `paired_at`** |

### 1.4 Storage and integrity
`<data>/ml_shadow/pairs/pairs.jsonl` follows the `ai_scan_ledger.ScanLedger` pattern:
- append-only and **never rotated**;
- each write is one whole sealed line (write + flush + fsync under a lock), and a torn last line is closed off before
  the next append;
- **restart identity:** `record_hash` covers the content only, never `paired_at`, so the same pair content replayed
  after a restart hashes identically and is a no-op. A `backfill_v1` row does **not** hash like the corresponding live
  row, because `source` and `provenance` intentionally differ (§1.5). A real content change under one `pair_key`
  (a different `record_hash`) is refused and counted as `duplicate_pairs`, and nothing is written;
- reads verify `record_hash`; an edited or corrupt line is skipped and counted, never raised;
- rows pass a `sanitize_row('pair', ...)` allow-list (no secrets, raw ids hashed).

### 1.5 Backfill (Codex ruling)
Backfill is allowed **only** when a surviving join can be **deterministically reconstructed** into the full
`ml_shadow.pair.v1` schema, with measured, cost-complete fields and immutable `source_files` hashes. Such rows are
tagged **`source = backfill_v1`**. Counting rules:
- backfill rows **never count toward the prospective gate** (≥ 200 new closes, §5);
- they count toward retrospective training **only if** they pass every rule a live pair must pass: integrity, cost
  completeness, measured basis, provenance, a `code_id` and an eligible family. Otherwise they are
  diagnostics/engineering only;
- the **180 orphaned labels are never synthesized**: no decision or feature row is invented for them, and they stay a
  historical diagnostic count.

Clarifications (Codex 6455705215):
- **Separate backfill epoch.** Pre-live `backfill_v1` rows reference their own immutable, exclusive-create
  `<data>/ml_shadow/pairs/backfill_epoch.json`. It holds `reconstruction_code_id`, `source_file_hashes` (the full
  sha256 list), `created_at`, and its own `backfill_epoch_hash`. Backfill rows are **never compared against or backdated
  into the live epoch** (`pair_epoch.json`), and they **do not affect the live-epoch must-be-zero counters**.
- **Existing pair key.** Backfill **never overwrites an existing live pair**. If the `pair_key` already exists, the
  backfill row is skipped as a no-op and counted as **`backfill_existing_skipped`**, not `duplicate_pairs`. Repeating
  the backfill of a backfill row is idempotent **only when its canonical content hash matches**. A different hash is
  refused and counted.
- **Management reconstruction.** A backfill row is training-eligible **only when its effective open-time management is
  exactly reconstructible from immutable decision/config/build evidence**, with those evidence hashes recorded in
  `provenance` (`mgmt_evidence_hashes`). Otherwise it is **diagnostics-only and counted** (`backfill_mgmt_unreconstructible`).

### 1.6 Code identity
`code_id` binds three things:
- the **packaged build identity**: `build_info.BUILD_ID`, written by the installer (`installer.ps1`, `build_info.py` in
  the staged source; read by `app.py`);
- the **git source commit**: `source_commit` in the installer's `build.json`;
- a **module-bundle hash**: sha256 over the sorted `(path, sha256)` list of the modules that produce a pair as packaged
  (`engine.py`, `actual_costs.py`, `ml_shadow/*.py`).

A pair without a packaged build (`BUILD_ID == 'dev'`) or without a source commit is for diagnostics/engineering only.

### 1.7 Effective management snapshot
Eligibility and horizon come from the **row's own snapshot, never from the registry**. When the lot opens, ML-PAIR-01
records the lot's effective `mgmt`, as the engine will apply it (`engine.py` `closing = ... l['mgmt'].get('max_bars')
and not run ...`), with the durable decision in `labelable_decisions.jsonl`. The pair row copies it, and it is never
edited:
- `effective_max_bars`: `lot['mgmt']['max_bars']`;
- `runner`: true when `lot['mgmt']` has a `runner` key;
- `overlay_id` and `overlay_hash`: the `ROTATION_MGMT_OVERLAY` entry applied to the sleeve (null when none) and the
  sha256 of its canonical JSON;
- `mgmt_hash`: the sha256 of the canonical effective `mgmt`; `canonical_mgmt_hash`: the same hash for the registry
  `strategies.STRATEGIES[key]['mgmt']`;
- `tf` and `horizon_h = effective_max_bars × tf`.

**Management drift source (Codex 6455705215).** `mgmt_close_hash` is the hash of the management snapshot in the
measured close/history row. `mgmt_changed` is true when `mgmt_close_hash ≠ mgmt_hash`. A **missing `mgmt_close_hash`
or a mismatch with the immutable open snapshot excludes the pair from training** and is counted (`mgmt_close_missing`,
`mgmt_changed`). It **never silently inherits registry management**.
Pairs with `runner = true`, an overlay, or `mgmt_hash ≠ canonical_mgmt_hash` (a **noncanonical override**) are
**excluded and counted** (`runner`, `noncanonical_mgmt`).

**v1 excludes `pending` / trailing-entry decisions entirely.** Their horizon from the decision includes an entry wait
(`engine.py` `te.max_bars`, default 3 bars) that no signed contract freezes yet. They are counted as `pending_excluded`
and wait for a separately signed **pending-horizon contract**.

### 1.8 Durable pairing epoch
`<data>/ml_shadow/pairs/pair_epoch.json` is written **once, by exclusive create**, when the first installed ML-PAIR-01
build handles its first labelable decision. It holds:
- `build_id`, `source_commit` and `bundle_hash` (the §1.6 `code_id` of that installed build);
- `first_decision_candle`;
- `created_at`;
- `epoch_hash`, the sha256 of the canonical body without `epoch_hash`.

It is never rewritten, so it cannot drift or be backdated. A pair whose `candle_time` is before `first_decision_candle`
is refused, and `first_decision_candle` must not be later than `created_at`. Every pair row and every run manifest
carries its `epoch_hash`. A missing epoch while pairs exist, an `epoch_hash` that does not verify, or a pair row whose
`epoch_hash` differs **fails pairing** (`PAIRING_DEFECT`). A reset needs a **new signed epoch/archive operation**: the
old `pairs.jsonl` and epoch are archived and hash-recorded, then a new epoch is created under a newly signed contract
version.

## 2. Diagnostics — second

Exposed in `health['ml_shadow']['pairs']` and in the dataset manifest.

**Epoch.** The must-be-zero counters are defined relative to the **ML-PAIR-01 epoch**: `pair_epoch_start` is
`first_decision_candle` in the immutable `pair_epoch.json` (§1.8), which every manifest references by `epoch_hash`. Labels orphaned before the
epoch are the diagnostic `historical_orphans` (Codex-reported 180). They never block training.

For labelable decisions with `candle_time ≥ pair_epoch_start`:
- `orphan_pairs` (measured close with no durable decision) **must be 0**;
- `duplicate_pairs` (refused conflicting `pair_key`) **must be 0**;
- `missing_decision` **must be 0**.

Also counted:
- critical feature completeness per feature (present / total), with pairs missing a critical feature (`atr_pct`,
  `ema_slope`, `regime`, `session`, `family`, `side`) counted explicitly;
- `non_training_closes` by basis (`acknowledged`, `booked`);
- closes not paired because costs are incomplete;
- `measured_wait` overflow (`MEASURED_WAIT_MAX = 500`);
- censored taken decisions;
- `backfill_v1` rows, split into training-eligible and diagnostics-only, plus `backfill_existing_skipped` and
  `backfill_mgmt_unreconstructible` (backfill counters never enter the live-epoch must-be-zero counters);
- `mgmt_close_missing` and `mgmt_changed`.

Training is refused while any must-be-zero counter is non-zero.

## 3. Training — only then

### 3.1 Eligible strategies and frozen label horizons
The label horizon is frozen **ex ante** from each strategy's time exit (`mgmt.max_bars` × candle length). It is never
the observed holding time, which can read the future or the holdout and has no bound for some strategies. The time exit
fires at `engine.py` `closing = ... max_bars and not run and bars >= max_bars`, so a lot whose effective management has
a `runner`, or whose `max_bars` was changed by `ROTATION_MGMT_OVERLAY`, has no frozen cap. A pair is eligible only if
its own §1.7 snapshot shows `runner = false`, no overlay, `mgmt_hash = canonical_mgmt_hash` and
`effective_max_bars` equal to the table below. Its horizon is the snapshot's `horizon_h`. Every other pair is excluded
and counted. A pair whose `closed_at − candle_time` exceeds its frozen horizon is excluded as `horizon_breach`.

**Eligible (capped)** — strategy, timeframe, cap, and the `strategies.py` line of the cap:

| Family | Strategy | tf | max_bars | Horizon | Source |
|---|---|---|---|---|---|
| Trend | `exp_trend` | 4h | 180 | 720 h | `strategies.py:468` |
| Range | `exp_range` | 4h | 30 | 120 h | `strategies.py:474` |
| Range | `exp_vwaprev` | 4h | 30 | 120 h | `strategies.py:511` |
| Breakout | `exp_breakout` | 4h | 60 | 240 h | `strategies.py:479` |
| Breakout | `exp_structure` | 15m | 24 | 6 h | `strategies.py:490` |
| Momentum | `pullback_rsi` | 4h (sleeve default) | 60 | 240 h | `strategies.py:451` |
| Scalp | `exp_scalp` | 15m | 16 | 4 h | `strategies.py:484` |
| Other | `range_scalp` | 15m | 16 | 4 h | `strategies.py:518` |
| Other | `failed_breakout_scalp` | 15m | 12 | 3 h | `strategies.py:525` |
| Other | `momentum_burst_scalp` | 15m | 4 | 1 h | `strategies.py:531` |
| Other | `quick_bank_runner` | 15m | 12 | 3 h | `strategies.py:357` (`QB`) |

A (strategy, tf) pair not in this table, for example `pullback_rsi` on another timeframe, is excluded.

Family maximum horizon: **Trend 720 h, Breakout 240 h, Momentum 240 h, Range 120 h, Scalp 4 h, Other 4 h.**

**ML v1 = currently capped strategies only.** The uncapped strategies below are **excluded from v1 training** until a
fixed causal horizon exists (they have no `max_bars`, and exit on trailing stops or signals). This includes F01
`ema_mom` and F12 `breakout_pyramid`. The exclusion **does not block their strategy or PAPER path**. ML-PAIR-01 must
**not invent labels** for them. Label-only horizons for them are the subject of the DEFERRED follow-up contract
**`ML-HORIZON-02`**: deterministic causal label-only horizons, with **no runtime trade-management change**.

Excluded (uncapped):
- `ema_st`, `ema_mom`, `donchian_ens` (Trend);
- `breakout_pyramid`, `squeeze_tp`, `bear_breakdown` (Breakout);
- `hot_coin`, `exp_mom_short` (Momentum);
- `rotation` (the whole Rotation family).

**Excluded regardless of cap:** `dca_dip` and `exp_rip_sell_dca` (Dip-DCA). Their basket R comes from a multi-entry
basket and their backtest is marked `UNVERIFIED_BT01`.

### 3.2 Dataset
Only `pairs.jsonl` rows are used (no ledger joins), and only those with `source = live`, or `source = backfill_v1`
that passed §1.5. Exclusions are each counted: ineligible strategy, `horizon_breach`, missing critical features, and a
dev `code_id`.
- Target: `y = 1` if `net_r > 0`, else `0`.
- Cost revision: for each `decision_id`, the latest `cost_rev_id` with revision time ≤ the dataset cut `T_cut`.
- Censoring: taken decisions of an eligible strategy with `candle_time + frozen horizon ≤ T_cut` and no pair are
  **censored**. If censored > **5%**,
  the result is `INSUFFICIENT_DATA`.

Before any fit, the run writes a manifest with:
- the sha256 of `pairs.jsonl` and `T_cut`;
- counts per include/exclude reason;
- the sorted `(decision_id, cost_rev_id, record_hash)` list and its sha256 (`dataset_digest`). `paired_at` is never
  part of it;
- the `epoch_hash` of `pair_epoch.json`;
- the feature schema and its sha256, and the label definition `ml_train_01.y_netr_gt0.v1`;
- split dates, the frozen horizon table, per-fold purge counts, seeds and code commit.

### 3.3 Split — purged chronological walk-forward by Cairo day
- Rows are ordered by `(candle_time, decision_id)`, with **never a random shuffle**. Blocks are whole Cairo days
  (`trainer.cairo_day`).
- The development region is the first **80%** of Cairo days. The **sealed holdout** is the last **20%** of Cairo days.
  Training, calibration and selection code **never reads the sealed holdout**. Its digest is fixed in the manifest, and
  it is opened once, after everything is frozen.
- Inside development there are at least **4** expanding future folds (`walk_forward_splits`), each with ≥ **50**
  labels.
- **Per-row strategy-horizon purging (Codex 6453844773). There is no single global embargo.** A training or
  calibration row is kept for a fold only if **both** of these end **before the boundary** (the future fold's start,
  or the sealed holdout's start):
  - its own frozen strategy horizon, counted from the decision time: `candle_time + horizon_h ≤ boundary` (the
    half-open interval `[candle_time, candle_time + horizon_h)` ends before it);
  - its actual close: `closed_at < boundary`.
- All intervals use **`[start, end)` semantics**. An episode is `[candle_time, closed_at)`.
- **Sealed holdout: purge without reading it.** Against `holdout_start`, each development row is purged using only its
  own frozen horizon and its own `closed_at < holdout_start`. **Holdout rows are never inspected** to purge, check or
  size anything.
- **Exact overlapping symbol episodes are purged inside development only.** A training row whose episode overlaps a
  same-symbol episode of the development test fold is dropped. Cross-episode checks use **development-fold metadata
  only**.
- **Shorter-horizon rows do not inherit `exp_trend`'s 30-day exclusion.** A 4 h scalp row is purged against its own
  4 h horizon only.
- Each horizon comes from the frozen §3.1 table. **No future or holdout duration is read to choose a horizon.**
- The fold, day and count gates are unchanged. **If per-row purging leaves too few rows, the outcome is
  `INSUFFICIENT_DATA`**, and no gate is ever relaxed.

### 3.4 Model and calibration
- **v1 is ONE pooled, regularised model.** It is an L2 logistic regression with `l2 = 1.0` and no hyper-parameter
  search. **There are no per-cell models in v1**; per-cell models can only come in a new contract with adequate samples.
- **Feature schema `ml_train_01.features.v1`:** the 15 `ml_shadow.features` names (13 numeric, z-scored on the training
  fold, plus a missing-mask, and one-hot `regime` and `session`) **plus explicit categorical `family` and `side`**, taken
  from the decision row. The artifact's `feature_names_sha256` covers this full ordered list.
- **Reporting:** per family, per side and per regime.
- **Naive prior it must beat:** the training-window base rate.
- **Isolated calibration:** Platt scaling fitted only on pooled out-of-fold development predictions. It is never fitted
  on rows the base model was trained on, and never on the holdout. Isotonic is not allowed in v1 (it needs ≥ 5,000
  labels).
- **Reported metrics:** AUC, Brier, log-loss, calibration skill vs the prior, and ECE over 10 equal-width bins (adjacent
  bins merged until each has ≥ 50). Each is reported per fold, per family, per side and per regime.

### 3.5 Leakage gates (any failure → `LEAKAGE_SUSPECT`, no artifact)
1. Feature time ≤ decision time: `candle_time + tf ≤ ts_utc` for every pair (frames hold closed candles only), and the
   feature keys equal the v1 feature schema exactly.
2. Per-row horizon purging and episode purging are asserted per fold, with zero sealed-holdout reads (index guard + unchanged holdout digest).
3. Label-shuffle null across Cairo-day blocks, **200** permutations: the mean null skill must be ≤ **0.005**, and the
   real skill must be above the null's 95th percentile.
4. Future-feature canary: a label-derived feature is refused by gate 1. With gate 1 bypassed in the test only, a
   single-feature AUC > **0.90** must be flagged.
5. No cost revision after `T_cut` is used.

### 3.6 Artifact
The artifact is `{"body": {...}, "sha256": ...}`. The body holds:
- `contract: "ML-TRAIN-01/v1"`, `model_version: "ml_train_01.logit_platt.v1"`, `authority: "none"`;
- `status` (`UNVERIFIED` | `CALIBRATED_SHADOW`);
- `feature_schema` and `feature_names_sha256`;
- spec, weights, bias, Platt `a`/`b`, reliability bins and metrics;
- `dataset_digest`, `manifest_sha256`, `code_id`, split, `t_cut` and `created_at`.

It is stored at `<data>/ml_shadow/artifacts/ml_train_01/<dataset_digest[:16]>.json`, outside the repo. It is
**immutable**: created exclusively and never overwritten.

## 4. Shadow-only authority

The artifact is advisory metadata. **Runtime authority is DEFERRED.** If a separate owner-approved contract ever grants
authority, ML may only **veto or reduce** risk and will **never increase** it: no larger size, no extra entry, no looser
stop, no settings change. The defaults stay safe:
- default OFF and PAPER-only;
- the engine never imports the trainer;
- a future read-only UI flag, `ML_TRAIN_UI`, defaults to `false`.

## 5. Pre-registered promotion thresholds (Codex 6452728797, verbatim)

These thresholds may **tighten** during independent review but **never weaken silently**. Any change is a new signed
version.

| Gate | Threshold |
|---|---|
| Paired labels | **≥ 1,000** |
| Per family-side cell | **≥ 100** |
| Future folds | **≥ 4**, each **≥ 50** labels |
| Cairo days | **≥ 30**, and multiple regimes (**≥ 2**) |
| Out-of-fold AUC | **≥ 0.55**, plus positive calibration skill |
| Selected tranche net R | positive, day-block bootstrap lower CI **> 0** |
| Tranche fold stability | positive in **≥ 3 of 4** folds |
| Cost stress | net R non-negative at **1.5×** variable cost (fees + slippage + funding) |
| Activity retained | **≥ 30%** of trades |
| Concentration | **≤ 35%** |
| Prospective shadow | then **≥ 200** new cost-complete shadow closes over **≥ 14** Cairo days (`source = live` only) |

Build proposes these definitions for Codex to freeze:
- the **selected tranche** is the set of trades whose calibrated p is above a cut chosen inside development only;
- **concentration** is the single largest symbol's share of the tranche's positive net R;
- the **day-block bootstrap** uses 2,000 reps and a 95% CI.

The promotion ladder, in order:
1. `CONTRACT`: this contract is signed.
2. `PAIRED_LEDGER`: ML-PAIR-01 is accepted and the epoch counters are zero.
3. `ARTIFACT_EXISTS`: a completed, leakage-clean fit wrote an immutable artifact with status `UNVERIFIED`.
4. `CALIBRATED_SHADOW`: every §5 gate passes on development and on the sealed holdout.
5. `PROSPECTIVE_SHADOW`: ≥ 200 live closes over ≥ 14 days.
6. `VETO_CONSIDERATION`: **DEFERRED / out of scope**, and risk-reducing only.

**Today the dataset fails at step 1.** There are 128 surviving joins (Codex-reported) against a minimum of ≥ 1,000
paired labels. A training run today must output `INSUFFICIENT_DATA`, and pairing must be fixed first.

## 6. Failure modes (fail closed)

The run outcome takes one of two forms, and they never overlap.

**A. Structural, data, leakage or source failure → run record only, NO model artifact.** The run writes a small
fail-closed run record (`status`, `reason`, counts, manifest sha, `epoch_hash`) and stops. No model file is written on
any of these:
- `PAIRING_DEFECT`: a §2 epoch counter is non-zero;
- `INSUFFICIENT_DATA`: a §5 count gate (labels, cells, folds, days, regimes) fails, censoring is too high, or a fold
  has only one class;
- `DATASET_CONFLICT`: a conflicting `pair_key`;
- `LEAKAGE_SUSPECT`: a §3.5 gate fails;
- `SOURCE_UNREADABLE`: more than 0.1% of lines were skipped;
- `ARTIFACT_EXISTS`: the immutable target exists with a different body.

**B. Completed, leakage-clean fit that misses a predictive or promotion threshold → immutable `UNVERIFIED` artifact.**
This covers OOF AUC, calibration skill, the tranche, cost-stress, activity and concentration gates. Such a fit **may**
write an immutable artifact with status `UNVERIFIED`. Only a fit that passes every §5 gate is `CALIBRATED_SHADOW`.

## 7. Implementation tickets (in order)

1. **ML-PAIR-01 paired ledger.** Write `labelable_decisions.jsonl` at decision time and append to `pairs.jsonl` at the
   measured close (§1.1). Add `sanitize_row('pair')`, `record_hash`, idempotency and `code_id` (§1.6). Tests cover
   rotation survival, restart, conflicting replay, a torn line, skipped decisions never labelled, and acknowledged and
   booked closes never paired.
2. **ML-PAIR-02 label basis + cost revision id.** Label rows today carry no `net_basis` and no `cost_rev_id`
   (`labels.build_label_record`, `sanitize._label`), so #201's `(decision_id, cost_rev_id)` join cannot be enforced.
   This ticket adds both.
3. **ML-BACKFILL-01.** The deterministic `backfill_v1` reconstruction of surviving joins (§1.5), with source-file
   hashes. Orphans are never synthesized.
4. **ML-DIAG-01 diagnostics.** The §2 epoch counters and strata in health and the manifest, including `measured_wait`
   overflow and censored taken decisions (both are silent today).
5. **ML-TRAIN-01a trainer to contract.** Rewrite `trainer.train` to read `pairs.jsonl` only, with:
   - the v1 feature schema (`features.CATEGORICAL` has no `family`/`side` today) and the frozen horizon table;
   - a sealed holdout, calibration and the prior baseline;
   - Brier, log-loss, ECE and AUC gates, the shuffle null and the canary;
   - conflict refusal instead of "first label wins";
   - an immutable artifact (`out_path` is overwritten today), a manifest, a digest and `code_id`.
6. **ML-TRAIN-01b scorer guard.** `scoring.score` returns a raw `p` and `grade` today. It must require
   `authority: "none"` and the `ml_train_01` model version, and show `UNVERIFIED` unless the status is
   `CALIBRATED_SHADOW`.
7. **ML-SHADOW-UI (later).** A read-only display behind `ML_TRAIN_UI`, after the prospective window.

**DEFERRED follow-up contract:** **`ML-HORIZON-02`**, deterministic causal label-only horizons for the uncapped
strategies, with no runtime trade-management change. Until it is signed they stay out of ML training.

## Open questions for Integration

- **Q1** (settled by Codex 6453844773: `exp_trend` stays in v1, with per-row horizon purging instead of a global
  embargo.)
- **Q2** (closed by Codex 6455506817: v1 excludes pending / trailing-entry decisions entirely, until a separately signed
  pending-horizon contract.)
