# ML-TRAIN-01 — time-split closed-label baseline + calibration artifact (shadow-only)

**Status: DRAFT — unsigned.** Contract only: no training run, no data read. Every number below is a *proposal* for
Integration (Codex) to freeze; nothing here is accepted until Codex signs the twin
`ML_TRAIN_01_CONTRACT_v1.json` (`status` → `FROZEN`, `signed_by`, `frozen_at`). Derived from `origin/master` @ `945f997`
code only (`ml_shadow/*`, `engine.py` ML hooks, `actual_costs.row_view`, `strategies.py` management) and
`AI_SCAN_01.md` (#199/#201). Roadmap: Codex 6452494807 (NEXT), gates: Codex 6452143340.

PATH: Phase 10 ML-in-shadow → ML-SHADOW-01 ledgers (live) → **ML-TRAIN-01 offline artifact** → read-only UI (later).
DEFERRED: any veto, sizing, order, risk or settings use of the model.

## 1. Scope and authority

- Output is an **offline, advisory metadata artifact**. It has **no authority**: no order, entry, exit, sizing,
  risk-veto, rotation or settings effect. The artifact carries `"authority": "none"`; any consumer that finds another
  value must refuse it.
- **PAPER-only.** Inputs are the PAPER ML-shadow ledgers; no mainnet/testnet account call, no key, no network.
- **Default OFF.** The trainer runs only when invoked by hand / an explicit research command; the engine never imports
  it (already true for `ml_shadow.trainer`). A future UI flag `ML_TRAIN_UI` defaults to `false`.
- The engine hooks (`_ml_decision`, `_ml_label`) are unchanged by this ticket.

## 2. Dataset

### 2.1 Sources (code paths)
- Decisions: `ml_shadow.ledger.build_decision_record` via `engine._ml_decision` → `<data>/ml_shadow` decision ledger
  (`JsonlLedger`, rotated, `ML_MAX_BYTES=2_000_000`, `ML_MAX_FILES=5`).
- Labels: `ml_shadow.labels.build_label_record` via `engine._ml_label`, called only from `engine._measured_done`
  (FILL-PNL-02), i.e. once `actual_costs.row_view(rec)['status']` is `measured`, `acknowledged` or (legacy, untracked
  row) `booked`. Rows still `pending` wait in `state['measured_wait']` (cap `MEASURED_WAIT_MAX=500`; overflow drops the
  label).

### 2.2 A closed label qualifies only if ALL hold
1. Decision row: `kind == 'decision'`, `schema == 'ml_shadow.v2'` (v1 rows excluded and counted), `labelable(row)` is
   true (taken, or pending-then-filled; never skipped).
2. Label row: `kind == 'label'`, finite `net_r`, parseable `closed_at`, `closed_at > candle_time`.
3. **Net basis is `measured`.** `acknowledged` (operator-typed net) and `booked` (legacy) are excluded. *Today the label
   row does not carry its basis — see gap G1; until G1 lands this condition is enforced only by a frozen cutover
   timestamp (open question Q1), and labels before it are excluded.*
4. Family is not `Dip-DCA` (`dca_dip`, `exp_rip_sell_dca`): DCA basket R is unverified (`UNVERIFIED_BT01`) and
   multi-entry; excluded and counted.
5. Costs complete: fees, funding and fill prices measured (`actual_costs.measured(rec)['complete']`).

### 2.3 Join, dedup, conflicts
- Join key: **(`decision_id`, `cost_rev_id`)**, matching #201's rule (outcome rows are append-only actual-cost
  revisions). For each `decision_id` the label used is the **latest cost revision with revision time ≤ dataset cut
  `T_cut`**. *No revision id exists in ml_shadow labels today (G1); until then `cost_rev_id = 0` and any second label
  for the same `decision_id` is a conflict.*
- Exact duplicate rows are idempotent. Two labels for one `(decision_id, cost_rev_id)` with different `net_r` →
  `DATASET_CONFLICT` (no artifact). The current trainer's "first label wins" is replaced by this rule.
- A label whose decision row is missing (rotated out / dropped) is an **orphan**: excluded and counted, never relinked
  (Codex 6254070622).
- Binary target `y = 1` iff `net_r > 0` (measured net after fees/funding/fills). `net_r` is kept for ranking reports.

### 2.4 Censoring
`T_cut` is fixed before the run. Taken/filled decisions with `candle_time ≤ T_cut − embargo` and no qualifying label are
counted as **censored**. If censored > **5%** of eligible decisions → `INSUFFICIENT_DATA (censoring)`; short holds
would otherwise be over-represented.

### 2.5 Manifest and digest (reproducibility)
Before any fit the run writes a dataset manifest: sha256 of every source ledger file read, `T_cut`, row counts per
include/exclude reason, the sorted list of `(decision_id, cost_rev_id, net_r)` and its sha256 = **`dataset_digest`**,
`FEATURE_NAMES` + sha256, label definition id `ml_train_01.y_netr_gt0.v1`, split boundaries (Cairo dates), embargo,
seeds and the code commit sha. Same inputs + same code ⇒ same digest and byte-identical artifact body (except
`created_at`).

## 3. Time split

- Order by `(candle_time, decision_id)`. **No shuffling anywhere.** Blocks are whole Cairo days
  (`trainer.cairo_day`), so same-candle decisions across sleeves stay together.
- **Development region** = first **80%** of Cairo days; **sealed test** = last **20%** of Cairo days, separated by the
  embargo. Its row digest is recorded before fitting; it is opened **once**, after model, regularisation and calibrator
  are frozen.
- **Walk-forward** inside development: **4** expanding folds (`walk_forward_splits`), embargo before each test block.
- **Embargo** = `max(3, ceil(max observed holding days))`, holding = `closed_at − candle_time` over qualifying labels.
  Several strategies have no time cap (trailing stops, rotation); the bounded maximum is `exp_trend` 180 × 4h = 30 days.
  The current trainer default `embargo_days=1` is below the label horizon and is not acceptable.
- **Purging** (labels overlap in time): a training row is kept only if it opened **and closed** before the cutoff
  (`test_start − embargo`); rows with unknown close are purged (already in `walk_forward_splits`).

## 4. Label sufficiency gate (before any fit)

| Gate | Proposed minimum |
|---|---|
| Qualifying labels overall | **1,000** (= `trainer.MIN_TOTAL`) |
| Per (family, side) bucket to be modelled | **100** (= `trainer.MIN_BUCKET`); smaller buckets excluded and listed |
| Per class (wins / losses) in development | **150** each |
| Per class in the sealed test | **50** each |
| Distinct Cairo days spanned | **30** |
| Distinct symbols | **10** |
| Per regime, for a regime stability line | **50** (else that line is `INSUFFICIENT`; not a fit block) |
| Sealed test labels for `CALIBRATED_SHADOW` | **500** (AI-SCAN-01 gate) |

**Today (~308 closed labels, before exclusions) fails the first gate** — about 3.2× short of a fit and about 8× short of
a calibrated artifact (500 sealed-test labels at a 20% test share needs ~2,500 qualifying labels). The expected output of
a run now is `INSUFFICIENT_DATA` with the counts, and that is a correct result.

## 5. Baseline model

- **Model:** L2-regularised logistic regression on the existing `ml_shadow.features` set (13 numeric z-scored with
  train-fold mean/std + missing-mask indicators, one-hot `regime`, `session`) — `trainer.fit_spec/design/fit_logistic`.
  `l2 = 1.0`, fixed. No hyper-parameter search in v1. GBT stays a diagnostic only (`MODEL_UNAVAILABLE` if absent).
- **Naive prior it must beat:** constant base rate of `y` in the training window. Also reported: per (family, side)
  base rate (diagnostic, not a gate).

## 6. Calibration artifact

- **Method (chosen pre-run): Platt scaling** fitted on the pooled out-of-fold walk-forward predictions of the
  development region (each prediction out-of-sample). Isotonic is not allowed in v1 (needs ≥ 5,000 calibration labels).
- **Evaluation on the sealed test only:** 10 equal-width reliability bins, adjacent bins merged until each has ≥ **50**;
  Brier, log-loss and Brier skill vs the prior; **ECE ≤ 0.05**; Brier improvement over the prior with a **time-blocked
  (Cairo day), symbol-clustered paired bootstrap, 2,000 reps, 95% CI wholly above zero**; log-loss below the prior's.
- **Artifact JSON** (`{"body": {...}, "sha256": sha256(canonical_json(body))}`), body fields:
  `contract: "ML-TRAIN-01/v1"`, `model_version: "ml_train_01.logit_platt.v1"`, `authority: "none"`,
  `status` (`UNVERIFIED` | `CALIBRATED_SHADOW`), `schema`, `feature_names` + `feature_names_sha256`, `spec`
  (mean/std/vocab), `weights`, `bias`, `calibration` (`method`, `a`, `b`), `reliability_bins`, `metrics`
  (Brier, log-loss, ECE, prior Brier/log-loss, bootstrap CI, shuffle-null result, per-fold, per-bucket, per-regime),
  `dataset_digest`, `manifest_sha256`, `code_sha`, `split` (dates, embargo, folds), `label_definition`,
  `t_cut`, `created_at` (UTC ISO; shown in Cairo time).
- **Storage:** `<data>/ml_shadow/artifacts/ml_train_01/<dataset_digest[:16]>.json` — outside the repo, never
  committed. **Immutable:** exclusive create; an existing file is never overwritten (a re-run with the same digest must
  reproduce the same body). Scorers verify the sha256 and refuse mismatch (as `scoring.score` already does).

## 7. Leakage gates (any failure → `LEAKAGE_SUSPECT`, no artifact)

1. **Feature time ≤ decision time:** for every row, `candle_time + tf ≤ ts_utc` (features come from closed candles;
   `engine` frames hold closed candles only) and the feature set equals `FEATURE_NAMES` exactly — no extra key.
2. **Embargo / purge checks:** every training row's `closed_at < cutoff`; no training row inside an embargo; the sealed
   test digest is unchanged from the pre-fit manifest; fitting code receives no sealed-test index.
3. **Label-shuffle null:** permute `y` across Cairo-day blocks, **200** permutations, same pipeline. The mean null
   Brier skill must collapse to **≤ 0.005**, and the real skill must exceed the null's 95th percentile.
4. **Future-feature canary:** a synthetic test injects the label (and a one-day-ahead return) as a feature; the run must
   refuse it (gate 1) and, with gate 1 bypassed in the test only, the single-feature AUC check (> **0.90** ⇒ flag) must
   flag it.
5. **Cost-revision leak:** a label revision timestamped after `T_cut` is never used.

## 8. Acceptance metrics and promotion ladder

| Rung | Meaning | Requires |
|---|---|---|
| L0 `CONTRACT` | this document | Codex freeze |
| L1 `ARTIFACT_EXISTS` (`UNVERIFIED`) | offline file, not shown anywhere | sufficiency §4 (first seven rows) + leakage §7 pass |
| L2 `CALIBRATED_SHADOW` | probability may be called calibrated in reports | L1 + sealed test ≥ 500, ECE ≤ 0.05, Brier CI > 0, log-loss < prior |
| L3 `SHOWN_IN_UI` | read-only, labelled "ML SHADOW · no authority" | L2 + Cowork independent reproduction of digest and metrics + Codex acceptance + `ML_TRAIN_UI` flag (default OFF) + forward window ≥ **14** Cairo days and ≥ **200** new labels with rolling ECE ≤ **0.08** and Brier skill > 0 |
| L4 veto consideration | — | **DEFERRED / out of scope.** Needs a separate owner-approved contract. |

Demotion: rolling ECE > 0.08 or no Brier gain over the prior in two consecutive resolved windows → back to `UNVERIFIED`
(same rule as AI-SCAN-01). Any feature/schema, label-definition or cost-model change opens a new artifact epoch.

## 9. Failure modes (fail closed)

The run never emits a partial artifact. It writes a small run record (`status`, `reason`, counts, manifest sha) and stops:
`INSUFFICIENT_DATA` (any §4 gate, censoring, no fold with both classes), `DATASET_CONFLICT` (§2.3),
`LEAKAGE_SUSPECT` (§7), `SOURCE_UNREADABLE` (corrupt/unreadable ledger file — a skipped corrupt line is counted and
> 0.1% of rows fails the run), `ARTIFACT_EXISTS` (immutable target present with a different body). A model that fails
the §6 gates is still written as `UNVERIFIED` and is never labelled calibrated.

## 10. Gaps in current code (follow-up tickets)

- **G1 (blocking) label basis + revision id.** `build_label_record`/`sanitize._label` carry no `net_basis`
  (measured/acknowledged/booked) and no `cost_rev_id`, so §2.2(3) and the #201 join cannot be enforced. Needs schema
  `ml_shadow.v3` label fields written by `_measured_done`.
- **G2 retention loses evidence.** The decision ledger rotates at 5 × 2 MB and deletes the oldest file; ~11k decisions
  are already near that bound, so old decisions vanish and their labels become orphans. Needs an append-only snapshot
  export with a sha256 manifest before any run.
- **G3 trainer vs contract.** `trainer.train` lacks: sealed test, Platt calibration, prior baseline, Brier/log-loss/ECE,
  bootstrap CI, shuffle null, canary, conflict rule (first label wins), v1/DCA/basis exclusions, censoring count,
  manifest/digest/code sha, data-derived embargo (default 1 day), immutable write (`out_path` is overwritten).
- **G4 scorer.** `scoring.score` reports the raw logistic `p` and `grade` with no calibration status and no
  `authority` check; it must refuse non-`ml_train_01` artifacts and mark `UNVERIFIED` probabilities.
- **G5 censoring visibility.** `measured_wait` overflow (500) silently drops labels; no count of taken decisions that
  never got a label is persisted.

## Open questions for Integration

- **Q1** Interim basis rule until G1: freeze a cutover timestamp (first build where every new lot is fill-tracked), or
  block ML-TRAIN-01 runs until G1 lands?
- **Q2** Should `acknowledged` labels ever qualify (e.g. as a separate stratum)?
- **Q3** Pool all non-DCA families in one model with (family, side) one-hots, or one model per bucket once each reaches
  the minimum?
