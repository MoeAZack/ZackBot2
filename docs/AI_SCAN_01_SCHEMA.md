# AI-SCAN-01 — signal-ledger schema and API detail (#199)

The owner-facing contract is `docs/AI_SCAN_01.md` (#200, Integration-owned, canonical head `9e9b529`). This file is the
Build-side record and API detail it is implemented against. `ai_scan.py` holds the immutable record schema and the pure
replay, and `tests/test_ai_scan.py` pins them. Nothing is wired into the engine or app yet. Implementation order after the
two #187 packets: ledger → scanner API/UI → calibration/evaluation harness.

## Records (`ai-scan-schema-v1`, append-only JSONL, one sealed `record_hash` each)

| kind | replay key | written when | content |
| --- | --- | --- | --- |
| `signal` | `signal_id` | the engine has decided (same point as the ML-SHADOW decision hook) | candle open, cutoff, decision instant, input ingest times, snapshot hash, deterministic plan, rules status/reason, cost estimate, input coverage, `snapshot_age_s`, `OK`/`INSUFFICIENT` + reasons |
| `shadow` | `signal_id` + `epoch` | asynchronously, after the decision | immutable `model_id`, `prompt_hash`, `sampling`, `feature_schema_hash`, `cost_model_hash`, the epoch, the snapshot/plan hashes it read; then either `OK` (raw-response hash, opinion `LONG`/`SHORT`/`NO_TRADE`, coverage, disagreement, probability block, bounded rationale) or `INSUFFICIENT` with `queue_full`/`timeout`/`budget_exhausted`/`remote_error` |
| `outcome` | measured: `signal_id` + `cost_revision_id` (`rev` orders only); simulated: `signal_id` + `cost_model_hash` | measured: at close (`rev` 0) and on each actual-cost restatement; simulated: from the causal replay | net R / PnL, exit reason, cost basis, partial exits, adds |

- **`signal_id`** is `ml_shadow.ledger.decision_id(sleeve, strategy, symbol, side, tf, candle_open)`. It is deterministic and
  restart-stable, and it joins the ML-SHADOW decision and label rows.
- **Time semantics:**
  - `candle_open` is the engine's `sg['time']`. Measured: it is the decision candle's open (`engine.py` `dec_open`).
  - `cutoff` is that candle's close.
  - `decided_at` is the engine's decision instant, which is ≥ the cutoff.
  - **`STALE_BARS = 2`** is a timeframe-relative freshness ceiling (Codex 6452143340 #1). The snapshot's last close must
    not be after the cutoff (`after_cutoff`), and its absolute age `snapshot_age_s = cutoff - last_close` is recorded and
    shown. Exactly 2 bars of the timeframe old is fresh; older is `INSUFFICIENT` with `stale_snapshot` (15m: 1,800 s,
    1h: 7,200 s, 4h: 28,800 s).
  - Every point-in-time input must have been **ingested** by `decided_at`: the snapshot (`available_at`), `funding`,
    `universe` membership and `exchange_rules`. A late or missing ingest time gives `INSUFFICIENT`, so restated or later-listed
    data cannot leak.
- **Display freshness:** the same ceiling applies to a shadow result. `display(view, now)` shows its absolute `age_s`
  (`now - created_at`); older than 2 bars of the signal's timeframe, it is shown as `INSUFFICIENT` with `stale` and no score.
  The record itself stays as historical evidence.
- **Epoch:** `sha256(model_id, prompt_hash, sampling, feature_schema_hash, TARGET, cost_model_hash)[:16]`. A change of any of
  these opens a new, unverified epoch. Vendor aliases are not identifiers.
- **Probability:**
  - The target is fixed as `TARGET`: P(net R > 0 after actual-model fees, funding and fills at the earlier of the strategy exit
    or its max-bar horizon).
  - A probability is kept only if `calibration_passes(c, epoch)` holds:
    - the calibration has an id and covers the same epoch and target;
    - its window is disjoint from fitting, walk-forward and the holdout;
    - n ≥ 500 resolved labels and ≥ 50 per bin;
    - ECE ≤ 0.05;
    - the paired-bootstrap 95% CI of the Brier improvement vs the base-rate predictor is wholly above 0;
    - it is not `demoted`, i.e. no rolling window has ECE > 0.08, and there are no 2 consecutive windows that fail to beat the
      base rate.
  - Otherwise the probability is dropped and the state is `UNVERIFIED`.
- **Outcomes:**
  - `measured` is the engine's own trade. Its join key is the immutable decision id (`signal_id`) **plus the actual-cost
    revision identifier** `cost_revision_id(actual_costs, net_basis=, cost_model_id=)` (Codex 6452143340 #2,
    6453104005): sha256 of the canonical JSON `[COST_REV_SCHEMA, [[field, float(value)] ...], net_basis, cost_model_id]`
    with `COST_REV_SCHEMA = 'ai-scan-cost-rev-v1'` and the pinned field list and order `COST_REV_FIELDS = (fees_actual,
    funding, pnl_gross, pnl_fill_adj, pnl_net, net_r)`. Only those fields are read, so timestamps, ids and presentation
    fields never change the id; any pinned field, the net basis, the cost-model id or a schema bump does. A missing or
    non-finite field has no identity (refused). A golden hash is pinned in the tests. Each revision joins to its own
    outcome under `view['revisions']`.
  - **Collision rule:** two different records under one (decision id, `cost_revision_id`) are a conflicting replay and
    refuse, in `fold` and in the ledger. Nothing is overwritten and neither is picked.
  - The monotonic `rev` (the history row's #196 `cost_rev`, `engine.py` `_row_changed`: 0 = booked, +1 per restatement) is
    kept **only for ordering and deduplication**: the highest `rev` is the current `measured`, two revisions sharing a `rev`
    refuse, and renumbering `rev` never changes which outcome a revision id joins. Its cost basis is `booked` or `measured`.
  - `simulated` is a deterministic causal replay under the frozen policy and cost model. It is keyed by that sha256 cost-model
    hash, and its cost basis is `modelled`.
  - Untaken, vetoed or capacity-skipped candidates get simulated outcomes only.
  - The two sources are stored, displayed and reported separately and are never merged.

## Fail-closed rules (all pinned)

- **INSUFFICIENT inputs.** Each of these gives `INSUFFICIENT` with the exact reason, never an inferred score:
  - a missing snapshot, features or last close;
  - a snapshot after the cutoff, or older than 2 bars of its timeframe;
  - an input ingested after the decision instant;
  - a non-finite or non-numeric feature;
  - a missing or malformed plan or targets;
  - a stop or target on the wrong side;
  - an unknown side or tf.

  A `None` feature only lowers coverage.
- **Signal refusals.** A signal is refused if it was decided before its candle closed. An `INSUFFICIENT` signal gets no shadow
  record.
- **Shadow refusals.** A shadow record is refused in each of these cases:
  - it was created before the decision;
  - it lacks an immutable identity (model id, sampling, sha256 prompt/feature-schema/cost-model hashes);
  - it is an `OK` result without a raw-response hash;
  - it read a different snapshot or plan hash;
  - it carries any plan, sizing, order, exit, risk, settings, eligibility or rotation field.

  A failed attempt and a later result for the same epoch conflict, so one read is kept per epoch.
- **Outcome refusals.** An outcome is refused in each of these cases:
  - it closed at or before the decision;
  - it is a measured outcome on a skipped decision;
  - the source has the wrong key (measured needs a sha256 `cost_revision_id` and an int `rev` ≥ 0; simulated needs a
    cost-model hash only);
  - its cost basis doesn't match its source.
- **Replay (`fold`).**
  - An exact duplicate is a no-op, which is what makes restarts idempotent.
  - A conflicting replay of the same key, a join to an unrecorded signal, an edited or forged record, or an unknown schema all
    raise `AIScanError`.
- **Queue.** `ScanQueue` is bounded, and `offer()` never blocks: a full or contended queue returns `False`, and the caller records
  `build_shadow_failure(..., 'queue_full')`.
- **Holdout.** The fitting and calibration paths read only through `fitting_view(records, holdout)`. It *refuses* (never silently
  filters) any signal whose decision candle lies inside the sealed `[start, end)`, and it refuses missing bounds.
- **No secrets.** Features, plan and costs may not carry credential-like or exchange/order/client/trade-id fields.

## API (report-only; flag `AI_SCAN`, default OFF, PAPER only)

| method | path | response |
| --- | --- | --- |
| GET | `/api/ai_scan` | flag on: `{on: true, rows:[display(view, now)...], ...}`, newest first. Optional `?sort=` with `age`/`symbol`/`coverage`/`disagreement`/`outcome_r` and `?limit<=500` |
| GET | `/api/ai_scan/<signal_id>` | one `display(view, now)` plus rationale, entry/stop/targets, the cost estimate, age, and the outcome history per source |

- **Flag off.** Both paths return `api_off()` = exactly `{on: false, rows: []}` when the flag is off (Codex 6452143340 #3;
  stable, pinned); no ledger is read. There is no POST, PUT or DELETE.
- **Supersession:** Codex 6452143340 supersedes 6374978304 / 6375106166 (ruling 6453104005); `docs/AI_SCAN_01.md`
  carries the same note and the same rules.
- **Labels.** The labels never merge:
  - `RULES` is the deterministic decision and plan;
  - `AI SHADOW` is one row per model epoch, with its `age_s`;
  - `UNVERIFIED` is shown in place of any uncalibrated probability; a calibrated one is shown with its target;
  - `INSUFFICIENT` is shown with its reasons, for the signal or for a failed shadow attempt.
- **Never exposed:** credentials, exchange or order ids, raw logs, raw state.

## Later stages (pinned at the schema level now; integration tests land with their stage)

- **Queue stage:** an engine-hook test that remote hangs, timeouts, a saturated queue and an exhausted spend budget leave the
  15-minute cycle and position management unaffected. Queue limit, worker timeout and spend budget are frozen in the
  preregistration.
- **Harness stage:**
  - the dedicated AI-SCAN-01 holdout, sealed by Codex with the hashed preregistration;
  - the B1 primary endpoint (net expectancy per resolved opportunity over B0);
  - ≥ 1,000 candidates and ≥ 300 resolved opportunities;
  - ≥ 80% of B0's trades/week;
  - ≤ 10% relative worsening of max DD or CVaR95;
  - Holm correction;
  - the gate metrics computed into the calibration record.
