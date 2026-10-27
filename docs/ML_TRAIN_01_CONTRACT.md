# ML-TRAIN-01 — paired training ledger, then time-split baseline + calibration (shadow-only)

**Status: DRAFT — unsigned.** Contract only: no training run, no data read by Build. Integration (Codex) freezes it by
signing the twin `ML_TRAIN_01_CONTRACT_v1.json` (`status` → `FROZEN`, `signed_by`, `frozen_at`). Built from
`origin/master` @ `945f997` code only (`ml_shadow/*`, the `engine.py` ML hooks, `actual_costs.measured/row_view`,
`strategies.py` management) plus `AI_SCAN_01.md` (#199/#201) and the AI-SCAN-02 ledger pattern (`origin/ai-scan-02`,
`ai_scan_ledger.py`).

Binding inputs: Codex roadmap 6452494807 (NEXT: ML-TRAIN-01), gates 6452143340, and the **data ruling 6452728797**, which
fixes the order of this contract: **(1) paired ledger → (2) diagnostics → (3) training → (4) shadow-only authority.**
No step starts before the one above it is accepted.

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

Cause (confirmed in code): the decision ledger is a rotating `JsonlLedger` (`engine.ML_MAX_BYTES = 2_000_000`,
`ML_MAX_FILES = 5`). `_rotate()` deletes the oldest file, so decision rows with their features disappear before the trade
closes; at close the writer (`ml_shadow/writer.py`, `_do_write`, label branch) finds no decision and counts
`missing_decision`. This is a durability defect, not only a sample-size problem.

**Skipped opportunities are not supervised outcomes.** A `skipped` decision never receives a label and must never be
labelled as a loss (or anything else) in this contract.

## 1. Paired training ledger (ML-PAIR-01) — first

### 1.1 Write point (exact code path)
`engine._measured_close` → (row still pending: `state['measured_wait']` → `engine._measured_followup`) →
`engine._measured_done` → `engine._ml_label` → `LedgerWriter.submit('label', row)` → writer thread
`LedgerWriter._do_write` (label branch). The paired row is appended **there**, on the writer thread, off the engine
cycle lock, and **only when** `actual_costs.row_view(rec)['status'] == 'measured'` and `actual_costs.measured(rec)
['complete']` is true. `acknowledged` and legacy `booked` closes are counted, never paired (open question Q2).

### 1.2 Keeping the decision alive until close
At decision time (`engine._ml_decision` → `submit('decision')`), a decision whose status is `taken` or `pending`
(`ledger.labelable`) is **also** appended to a durable, never-rotated store `labelable_decisions.jsonl`. Skipped
decisions stay only in the rotating ledger. The pairing step reads the decision from the durable store, so rotation can
no longer orphan a label. Size is bounded by the number of taken/pending decisions (Codex-reported 167 so far).

### 1.3 Row schema `ml_shadow.pair.v1`
| Field | Content |
|---|---|
| `kind`, `schema` | `"pair"`, `"ml_shadow.pair.v1"` |
| `pair_key` | idempotency key = `(decision_id, cost_rev_id)` |
| `decision_id`, `corr_id` | deterministic id (`ledger.decision_id`); hashed trade id |
| `decision` | the sanitized decision row as written at decision time: `ts_utc`, `candle_time`, `sleeve`, `strategy`, `family`, `symbol`, `side`, `tf`, `status`, `reason`, `rotation_group`, `features`, `missing`, `feature_hash`, `schema` |
| `label` | `closed_at`, `net_r`, `net_pnl`, `exit_reason`, `mfe_r`, `mae_r`, `target_before_stop`, `net_basis` (`measured`) |
| `costs` | from `actual_costs.measured`: `gross`, `fees_actual`, `funding_actual`, `fill_adj` (slippage vs booked fill), `risk_usd`, `complete`, `why` |
| `ids` | `code_id`, `decision_schema`, `label_schema`, `feature_names_sha256`, `cost_model_id`, `cost_rev_id` |
| `paired_at` | UTC ISO time of the append |
| `record_hash` | sha256 of the canonical row without `record_hash` |

### 1.4 Storage and integrity
`<data>/ml_shadow/pairs/pairs.jsonl`: append-only, **never rotated**, one whole sealed line per write (write + flush +
fsync under a lock), torn last line closed off before the next append — the `ai_scan_ledger.ScanLedger` pattern. Same
`pair_key` with the same `record_hash` = no-op (restart-safe); same key, different hash = refused and counted, nothing
written. Reads verify `record_hash`; an edited or corrupt line is skipped and counted, never raised. Rows pass a
`sanitize_row('pair', ...)` allow-list (no secrets, raw ids hashed).

## 2. Diagnostics — second

Exposed in `health['ml_shadow']['pairs']` and in the dataset manifest:
- `orphan_pairs` (measured close with no durable decision) **must be 0**;
- `duplicate_pairs` (refused conflicting `pair_key`) **must be 0**;
- `missing_decision` for labelable decisions after ML-PAIR-01 **must be 0**;
- critical feature completeness per feature (present / total), and pairs whose critical features
  (`atr_pct`, `ema_slope`, `regime`, `session`) are missing are counted explicitly;
- cost completeness: pairs with `costs.complete = false` are 0 by construction (they are not paired); closes skipped
  for incomplete costs, `acknowledged`, `booked` or `measured_wait` overflow (`MEASURED_WAIT_MAX = 500`) are each
  counted.
Training is refused while any must-be-0 counter is non-zero.

## 3. Training — only then

### 3.1 Dataset
Only `pairs.jsonl` rows (no ledger joins). Exclusions, each counted: family `Dip-DCA` (`dca_dip`,
`exp_rip_sell_dca`; DCA basket R is `UNVERIFIED_BT01`), rows with critical features missing. Target `y = 1` iff
`net_r > 0`. For each `decision_id` the latest `cost_rev_id` with revision time ≤ dataset cut `T_cut` is used. Taken
decisions with `candle_time ≤ T_cut − embargo` and no pair are **censored**; censored > **5%** → `INSUFFICIENT_DATA`.

A manifest is written before any fit: sha256 of `pairs.jsonl`, `T_cut`, counts per include/exclude reason, sorted
`(decision_id, cost_rev_id, record_hash)` and its sha256 = `dataset_digest`, `feature_names_sha256`, label definition
`ml_train_01.y_netr_gt0.v1`, split dates, embargo, seeds, code commit.

### 3.2 Split — purged chronological walk-forward by Cairo day
- Order by `(candle_time, decision_id)`; **never a random shuffle**. Blocks are whole Cairo days (`trainer.cairo_day`).
- Development region = first **80%** of Cairo days; **sealed holdout** = last **20%** of Cairo days. Training,
  calibration and selection code **never reads the sealed holdout**; its digest is fixed in the manifest and it is
  opened once, after everything is frozen.
- Inside development: at least **4** expanding future folds (`walk_forward_splits`), each test fold ≥ **50** labels.
- **Overlap embargo** = `max(3, ceil(max observed holding days))` (some strategies have no time cap; the capped maximum
  is `exp_trend` 180 × 4h = 30 days). **Purge:** a training row is kept only if it opened and closed before
  `test_start − embargo`.

### 3.3 Model and calibration
- **Regularised baseline:** L2 logistic regression on the `ml_shadow.features` set (13 numeric z-scored on the training
  fold + missing-mask, one-hot `regime`, `session`), `l2 = 1.0`. No hyper-parameter search in v1.
- **Naive prior it must beat:** the training-window base rate.
- **Isolated calibration:** Platt scaling fitted only on pooled out-of-fold development predictions — never on the rows
  the base model was fitted on, never on the holdout. Isotonic is not allowed in v1 (needs ≥ 5,000 labels).
- Reported: AUC, Brier, log-loss, calibration skill vs the prior, ECE over 10 equal-width bins (adjacent bins merged to
  ≥ 50), per fold, per family-side cell and per regime.

### 3.4 Leakage gates (any failure → `LEAKAGE_SUSPECT`, no artifact)
1. Feature time ≤ decision time: `candle_time + tf ≤ ts_utc` for every pair (frames hold closed candles only) and the
   feature keys equal `FEATURE_NAMES` exactly.
2. Embargo/purge asserts per fold; zero sealed-holdout reads (index guard + unchanged holdout digest).
3. Label-shuffle null across Cairo-day blocks, **200** permutations: mean null skill ≤ **0.005** and the real skill above
   the null 95th percentile.
4. Future-feature canary: a label-derived feature is refused by gate 1; with gate 1 bypassed in the test only, a
   single-feature AUC > **0.90** must be flagged.
5. No cost revision after `T_cut` is used.

### 3.5 Artifact
`{"body": {...}, "sha256": ...}` with `contract: "ML-TRAIN-01/v1"`, `model_version: "ml_train_01.logit_platt.v1"`,
`authority: "none"`, `status` (`UNVERIFIED` | `CALIBRATED_SHADOW`), features + sha, spec, weights, bias, Platt `a`/`b`,
reliability bins, metrics, `dataset_digest`, `manifest_sha256`, `code_sha`, split, `t_cut`, `created_at`. Stored at
`<data>/ml_shadow/artifacts/ml_train_01/<dataset_digest[:16]>.json`, outside the repo, **immutable** (exclusive
create, never overwritten).

## 4. Shadow-only authority

The artifact is advisory metadata. **Runtime authority is DEFERRED.** If authority is ever granted by a separate
owner-approved contract, ML may only **veto or reduce** risk, **never increase** it (no larger size, no extra entry, no
looser stop, no settings change). Default OFF, PAPER-only; the engine never imports the trainer; a future read-only UI
flag `ML_TRAIN_UI` defaults to `false`.

## 5. Pre-registered promotion thresholds (Codex 6452728797, verbatim)

These may **tighten** during independent review but **never weaken silently**; any change is a new signed version.

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
| Prospective shadow | then **≥ 200** new cost-complete shadow closes over **≥ 14** Cairo days |

Build's proposed definitions (for Codex to freeze): the selected tranche is the set of trades with calibrated
p above a cut chosen inside development only; concentration = the single largest symbol's share of the tranche's
positive net R. The day-block bootstrap uses 2,000 reps and a 95% CI.

Ladder: `CONTRACT` → `PAIRED_LEDGER` (ML-PAIR-01 accepted, diagnostics zero) → `ARTIFACT_EXISTS` (`UNVERIFIED`) →
`CALIBRATED_SHADOW` (all §5 gates on development + sealed holdout) → `PROSPECTIVE_SHADOW` (≥ 200 closes / ≥ 14 days) →
`VETO_CONSIDERATION` (**DEFERRED / out of scope**; risk-reducing only).

**Today the dataset fails at step 1:** 128 surviving joins (Codex-reported) against ≥ 1,000 paired labels, with 180
already orphaned. The correct output of a training run now is `INSUFFICIENT_DATA`, and pairing must be fixed first.

## 6. Failure modes (fail closed)

No partial artifact. A run writes a small run record (`status`, `reason`, counts, manifest sha) and stops on:
`PAIRING_DEFECT` (any §2 must-be-0 counter non-zero), `INSUFFICIENT_DATA` (any §5 count gate, censoring, a fold with one
class), `DATASET_CONFLICT` (conflicting `pair_key`), `LEAKAGE_SUSPECT` (§3.4), `SOURCE_UNREADABLE` (> 0.1% skipped lines),
`ARTIFACT_EXISTS` (immutable target present with a different body). A model failing §5 is written as `UNVERIFIED` only.

## 7. Implementation tickets (in order)

1. **ML-PAIR-01 paired ledger.** Durable `labelable_decisions.jsonl` at decision time; `pairs.jsonl` append at the
   measured close (§1.1); `sanitize_row('pair')`; `record_hash`; idempotency; tests for rotation survival, restart,
   conflicting replay, torn line, skipped-never-labelled, acknowledged/booked not paired.
2. **ML-PAIR-02 label basis + cost revision id.** Label rows today carry no `net_basis` and no `cost_rev_id`
   (`labels.build_label_record`, `sanitize._label`), so #201's `(decision_id, cost_rev_id)` join cannot be enforced; add
   both, plus a `code_id` (the engine has no build identity today; proposal: sha256 of `engine.py` + `ml_shadow/*.py`
   at start).
3. **ML-DIAG-01 diagnostics.** §2 counters in health and the manifest, including `measured_wait` overflow and censored
   taken decisions (both silent today).
4. **ML-TRAIN-01a trainer to contract.** `trainer.train` today: no sealed holdout, no calibration, no prior baseline,
   no Brier/log-loss/ECE/AUC gates, no shuffle null or canary, "first label wins" instead of conflict refusal, includes
   v1 and DCA rows, embargo default 1 day, overwrites `out_path`, no manifest/digest/code sha. Rewrite it to read
   `pairs.jsonl` only.
5. **ML-TRAIN-01b scorer guard.** `scoring.score` returns a raw `p` and `grade`; it must require `authority: "none"`,
   the `ml_train_01` model version, and show `UNVERIFIED` unless `CALIBRATED_SHADOW`.
6. **ML-SHADOW-UI (later).** Read-only display behind `ML_TRAIN_UI`, after the prospective window.

## Open questions for Integration

- **Q1** Backfill: can the 128 surviving joins be migrated into `pairs.jsonl` (marked `backfilled`, excluded from
  prospective counts), or does the paired ledger start empty?
- **Q2** Should `acknowledged` closes ever be paired (as a separate stratum)?
- **Q3** One pooled model with (family, side) one-hots, or one model per cell once each reaches 100?
