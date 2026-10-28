# AI-SCAN-03 — writer hook: immutable decisions joined to measured cost revisions (PAPER / shadow only)

**Status: DRAFT — unsigned.** This is a contract only. It adds no runtime code and calls no model. The machine twin
`ai_scan_03_contract.v1.json` sits next to this file and is authoritative. Tests read the JSON and check this file
against it. Signing sets `status` → `FROZEN`, plus `signed_by` and `frozen_at`. Assignment: Codex 6460083994 on #222.
It is built from `origin/master` @ `fe84dbb`. For reference only, it also reads #222 (`ai_scan_ledger.py`) and #226
(`ml_shadow/pairs.py`). This slice does not edit or import either of them. Before freeze or implementation, `builds_on`
is set to the **accepted merge SHAs** of #222 and #226; a superseded review head is never pinned (R8).

## 1. Scope

- The flag is `AI_SCAN`, **default OFF**. It is **PAPER only**: when `engine.live` is True the hook is refused before
  any record is built.
- **Authority: none.** The hook never gates, sizes, delays or cancels an order. Trading code never reads its output.
  It never writes settings or engine state and never holds the engine lock.
- **No model or API call, and no network access, in this slice.**

## 2. Where the writer hook fires

| Hook | Record | Call site (engine.py, read-only) | Fires when |
|---|---|---|---|
| `decision` | signal | `engine.Engine._ml_decision` | After the engine has decided a candidate (taken / skipped / pending), at every existing call in the scan loop. It fires whether or not ML_SHADOW is on. |
| `shadow` | shadow | `ai_scan.ScanQueue consumer` | Only when a shadow producer exists. **This slice has none, so it writes no shadow rows.** |
| `measured_close` | outcome | `engine.Engine._measured_done` | A taken decision's lot closes with `actual_costs.row_view(row).status == 'measured'` and `actual_costs.measured(row).complete`. |
| `cost_restatement` | outcome | `engine.Engine._row_changed` | `cost_rev` was bumped on a closed row that already reached a measured outcome, and the row is still measured and complete. |

What each hook receives:
- **Decision hook:** detached deep copies of `sleeve, strategy, family, symbol, side, tf, candle_open, decided_at,
  rules_status, rules_reason, snapshot, plan, inputs, costs`. These go to `ai_scan.build_signal`.
- **Close hooks:** `decision_id`, the five `COST_REV_FIELDS` read from the closed row, `cost_basis='measured'`,
  `cost_model_id`, `rev = row['cost_rev']`, `closed_at, exit_reason, partial_exits, adds`. These go to
  `ai_scan.build_outcome(source='measured')`.
- No hook keeps a reference to engine objects.

The decision id is `ml_shadow.ledger.decision_id(sleeve, strategy, symbol, side, tf, candle_open)`, which equals the
ai_scan `signal_id`. The lot carries it as `lot['ml_decision_id']`, stamped at `engine.Engine._ml_link`. The runtime
slice stamps it when ML_SHADOW **or** AI_SCAN is exactly True. **The flags are independent:** AI_SCAN works without
ML_SHADOW and the reverse, and neither is a prerequisite for the other (R1). A lot without the id produces no outcome
(`no_decision_id`).

## 3. Join: immutable decision → measured cost revisions

- **Record key** (it equals `ai_scan.record_key` / the `fold()` key):
  - signal: `(kind, signal_id)`
  - shadow: `(kind, signal_id, epoch)`
  - outcome: `(kind, signal_id, source, cost_revision_id, cost_model_hash)`
- The **economic key** is `(signal_id, cost_revision_id)`. **`rev` orders revisions only and is never part of the
  key.** The latest revision is the one with the highest `rev`.
- **A new cost revision appends. Nothing is ever rewritten.**
- An exact duplicate is a no-op, which makes restart replay idempotent.
- A **conflicting replay** (same key, different `record_hash`) is refused and nothing is written. The same applies to
  two costs that share one `rev`, and to an outcome with no recorded signal.
- A measured outcome needs a decision whose rules status is `taken`.
- **Only a `measured`, cost-complete close produces a measured outcome.** Booked, acknowledged, pending and incomplete
  closes never do. The generic accepted ai_scan schema stays compatible, but the writer hook passes net basis
  `measured` only (R2).
- **Cost identity:** `ai_scan.cost_revision_id`, schema `ai-scan-cost-rev-v2`, over
  `fees_actual, funding_actual, pnl_gross, pnl_fill_adj, risk_usd`. Net is
  `pnl_gross + pnl_fill_adj - fees_actual + funding_actual`, and net R is that net divided by `risk_usd`. The cost model
  id is `actual_costs.MEASURED_COST_MODEL_ID`, whose single definition arrives with #226.
- **Consistency with ML-PAIR-01 (#226):** its `pair_key = (decision_id, cost_rev_id)` maps one-to-one onto
  `(signal_id, cost_revision_id)`. Both use the same cost identity and the same close gate. Nothing here imports from an
  unmerged branch. Once #226 merges, `test_cost_identity_matches_ml_pair_01` pins the two equal.
- **Restatements (R3):** AI-SCAN appends every measured cost restatement, for audit. ML-PAIR-01 stays
  first-measured-close only in this milestone. Before training, a separate frozen ML-pair restatement policy must choose
  append-plus-latest-collapse or first-finalized revision, so one trade is never silently multiplied into training rows.

## 4. Never gates orders

The engine-thread hook only builds a sealed record inside `try/except`. It then offers the record to a **bounded,
non-blocking queue**. An offer never blocks and never raises; a full queue drops the record, counts `queue_full` and
opens a durable health gap (§6a).

A **dedicated, bounded AI-SCAN writer daemon thread** owns the scan ledger and does every disk write. It starts when
AI_SCAN is enabled, never holds the engine lock and never reads engine state. Each ledger has a single writer. Its
failures and lifecycle are not coupled to the `ml_shadow` LedgerWriter (R5).

Every exception in the hook or the writer is counted and swallowed, so none reaches order flow. The flag is read at
each call as `S.get('AI_SCAN') is True`.

Proof: `test_trading_byte_identical_with_ai_scan_on_off_and_failing_writer`. It replays the same PAPER cycle four
ways: with AI_SCAN off, on, on with a raising writer, and on with a full queue. Orders, history, state and order calls
must be byte-identical across all four.

## 5. Identity

Each record carries `code = {build_id, source_commit, bundle_hash}`. This needs `ai-scan-schema-v2`, and
`record_hash` covers `code`. Identity lives on **every record**, not in a once-written epoch, because one append-only
ledger spans several installed builds. No v1 runtime producer exists, so the bump lands before the first write (R4). The completeness rule is #226's `identity_gap`: a non-dev build id, a 40-hex source
commit from the matching build.json, and a 64-hex bundle hash. The bundle adds `ai_scan.py` and `ai_scan_ledger.py`.

When the identity is incomplete (reason `dev_build`, `source_commit` or `bundle_hash`):
- **signal:** written with `code_complete false`. It is never eligible for fitting, calibration or pairing.
- **shadow:** written with `code_complete false`. It never verifies.
- **outcome:** refused, nothing written. The hook counts `identity_incomplete` and records the identity reason.

## 6. Failure modes and counters (`health['ai_scan']`)

`signals`, `shadows`, `outcomes`, `replays`, `conflicts`, `queue_full`, `refused_live`, `not_taken`,
`non_measured_close`, `cost_incomplete`, `no_decision_id`, `unknown_signal`, `ambiguous_rev`, `identity_incomplete`,
`build_errors`, `write_errors`, `skipped_lines`, `gaps`.

When the flag is off, the hook returns before building anything and the ledger is untouched. A torn or edited line is
skipped on read and never raises. A disk error is swallowed, and the next record is tried.

### 6a. Queue gaps (R7)

Every `queue_full` drop opens or extends a **durable gap** `{from, to, dropped}`, written by the writer thread outside
the hot path and counted in `gaps`. Any downstream fit or calibration window that crosses a gap is **ineligible**, never
silently complete.

### 6b. Rotation prerequisite (R6)

Cowork measured about 1.06 s to fold 20k duplicate rows. **A runtime producer may not be enabled** until bounded,
append-only segmentation / rotation, with a manifest of segment hashes and idempotent replay across segments, is
implemented and tested. This is part of AI-SCAN-03 or a required predecessor, never post-producer cleanup.

## 7. Test plan (future implementation)

- `test_flag_off_builds_and_writes_nothing`
- `test_live_mode_refused_before_build`
- `test_trading_byte_identical_with_ai_scan_on_off_and_failing_writer`
- `test_hook_never_raises_into_order_flow`
- `test_offer_never_blocks_when_queue_full_or_contended`
- `test_writer_never_holds_engine_lock`
- `test_decision_hook_writes_one_signal_per_candidate`
- `test_measured_close_appends_outcome_keyed_by_cost_revision`
- `test_restatement_appends_new_revision_never_rewrites`
- `test_restart_replay_is_idempotent`
- `test_conflicting_replay_refused_unwritten`
- `test_same_rev_two_costs_refused`
- `test_booked_acknowledged_pending_never_measured`
- `test_outcome_only_for_taken_decisions`
- `test_incomplete_identity_refuses_outcome_flags_signal`
- `test_cost_identity_matches_ml_pair_01`
- `test_no_model_or_network_call`
- `test_flags_independent_either_flag_stamps_decision_id`
- `test_writer_thread_isolated_from_ml_shadow`
- `test_every_record_carries_code_identity_schema_v2`
- `test_rotation_segments_manifest_and_replay`
- `test_producer_enable_refused_without_rotation`
- `test_queue_drop_opens_durable_gap_and_window_ineligible`
- `test_api_view_unchanged_shape`

The JSON twin states what each one asserts.

## 8. DEFERRED

`runtime_producer_hook`, `remote_model_api_call`, `model_promotion`, `ui_controls`, `trading_authority`,
`simulated_outcomes`. Ledger rotation is **not** deferred: it is a prerequisite for the producer (§6b).

## 9. Codex rulings (6460621068) — open questions: none

- **R1 independent flags:** either flag stamps the decision id; neither requires the other.
- **R2 measured-only runtime:** generic schema compatible; the hook passes only `measured`.
- **R3 restatements:** AI-SCAN appends all measured restatements; ML-PAIR-01 stays first-close; a frozen ML-pair
  restatement policy is required before training.
- **R4 identity:** `ai-scan-schema-v2`, complete code identity on every record, bumped before first write.
- **R5 writer isolation:** dedicated bounded AI-SCAN writer, one per ledger, not coupled to ML_SHADOW.
- **R6 rotation prerequisite:** segmentation / rotation + manifest + replay before any producer is enabled.
- **R7 queue gaps:** durable gaps; windows crossing one are ineligible.
- **R8 builds_on:** refreshed to the accepted merge SHAs of #222 / #226 before freeze.
