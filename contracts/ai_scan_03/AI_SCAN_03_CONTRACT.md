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
- **Close hooks:** `decision_id`, `decided_at`, the five `COST_REV_FIELDS` read from the closed row, `cost_basis='measured'`,
  `cost_model_id`, `rev = row['cost_rev']`, `closed_at, exit_reason, partial_exits, adds`. These go to
  `ai_scan.build_outcome(source='measured')`.
- No hook keeps a reference to engine objects.

The decision id is `ml_shadow.ledger.decision_id(sleeve, strategy, symbol, side, tf, candle_open)`, which equals the
ai_scan `signal_id`. The lot carries it as `lot['ml_decision_id']`, stamped at `engine.Engine._ml_link`. The runtime
slice stamps it when ML_SHADOW **or** AI_SCAN is exactly True. **The flags are independent:** AI_SCAN works without
ML_SHADOW and the reverse, and neither is a prerequisite for the other (R1). A lot without the id produces no outcome
(`no_decision_id`).

**`decided_at` source:** `lot['ml_decided_at']`, the signal's `decided_at` in UTC ISO form, is stamped together with
`ml_decision_id` at `engine.Engine._ml_link`. The lot is persisted with engine state and copied onto the closed history
row, so both close hooks read `signal_id` and `decided_at` from in-memory data, never from disk on the trading thread.
A lot or row that has `ml_decision_id` but no `ml_decided_at` counts `no_decided_at` and makes **only that signal /
outcome** ineligible, with a named decision-time gap; an otherwise clean writer session stays clean, and nothing is
backfilled from disk on the hot path (D1). The canonical form is UTC ISO-8601 with exactly six-digit microseconds and
`Z` (e.g. `2026-10-28T08:00:00.000000Z`). Fold rejects an outcome whose `decided_at` differs from its signal's and counts
`decided_at_mismatch`.

## 3. Join: immutable decision → measured cost revisions

- **Record key** (it equals `ai_scan.record_key` / the `fold()` key):
  - signal: `(kind, signal_id)`
  - shadow: `(kind, signal_id, epoch)`
  - outcome: `(kind, signal_id, source, cost_revision_id, cost_model_hash)`
- The **economic key** is `(signal_id, cost_revision_id)`. **`rev` orders revisions only and is never part of the
  key.** The latest revision is the one with the highest `rev`.
- **Schema-v2 measured key (F1):** v2 measured outcomes add the integer `rev` to the record key:
  `(kind, signal_id, source, cost_revision_id, cost_model_hash, rev)`. A restatement back to earlier costs (A → B → A)
  therefore appends rev 0 / 1 / 2, and fold selects rev 2. The economic identity stays `(signal_id, cost_revision_id)`.
  The key above is today's accepted v1 key from #222.
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
ledger spans several installed builds. No v1 runtime producer exists, so the bump lands before the first write (R4).

**Cross-build replay (F2):** each record also carries a `content_hash`, computed over the record excluding only `code`
and `record_hash`. The same v2 key and content_hash with a different `code` counts `replays_other_build` and is a
no-op. A different content_hash is a conflict. For fitting, the signal's code and the selected outcome's code must
each be complete, but they need not match, because a position may span an upgrade. Both identities are kept in
provenance.

**Identity upgrade (G6):** an incomplete original row is never rewritten or silently blessed. A complete-identity
replay with an equal economic payload may append an explicit `identity_upgrade` record that references the original
`record_hash` (counted `identity_upgrades`). Fold uses it only when payload equality and the complete build identity
validate; a payload mismatch is a conflict. Audit keeps both rows, and until an upgrade exists the signal stays
ineligible.

The `identity_upgrade` record (schema v2) has the fields `kind, schema, signal_id, orig_record_hash, orig_record_key,
content_hash, code, event_at, writer_id, seq, record_hash`. Its key is `(kind, orig_record_hash)`. Replay is semantic (U1): an `upgrade_content_hash` covers `(orig_record_hash, original
content_hash, complete code identity)` and excludes the transport fields `writer_id, seq, event_at, record_hash`. The
same key and upgrade_content_hash is a no-op across sessions; the same original with a different original content_hash
or a different complete code is a conflict. Fold applies it only
when the original exists, the content hashes match, the original code is incomplete and the upgrade code is complete;
otherwise it is ignored and counted `identity_upgrade_invalid`. An upgrade whose original lies outside the current bounded
page is `unresolved_original`, not invalid; it resolves only after verified manifest pagination finds the original or
conclusively proves it absent (U2). **Fold never raises:** a conflict found on read poisons only that signal (ineligible,
counted `conflicts`). The completeness rule is #226's `identity_gap`: a non-dev build id, a 40-hex source
commit from the matching build.json, and a 64-hex bundle hash. The bundle adds `ai_scan.py` and `ai_scan_ledger.py`.

When the identity is incomplete (reason `dev_build`, `source_commit` or `bundle_hash`):
- **signal:** written with `code_complete false`. It is never eligible for fitting, calibration or pairing.
- **shadow:** written with `code_complete false`. It never verifies.
- **outcome:** refused, nothing written. The hook counts `identity_incomplete` and records the identity reason.

## 6. Failure modes and counters (`health['ai_scan']`)

`signals`, `shadows`, `outcomes`, `replays`, `conflicts`, `queue_full`, `refused_live`, `not_taken`,
`non_measured_close`, `cost_incomplete`, `no_decision_id`, `unknown_signal`, `ambiguous_rev`, `identity_incomplete`,
`build_errors`, `write_errors`, `skipped_lines`, `gaps`, `replays_other_build`, `gap_flush_failed`, `producer_disabled`, `identity_upgrades`, `coverage_gaps`, `no_decided_at`, `identity_upgrade_invalid`, `unresolved_original`, `decided_at_mismatch`, `manifest_full`.

When the flag is off, the hook returns before building anything and the ledger is untouched. A torn or edited line is
skipped on read and never raises. A disk error is swallowed, and the next record is tried.

### 6a. Queue gaps (R7)

Every `queue_full` drop opens or extends a **durable gap** `{writer_id, from, to, dropped}`, written by the writer thread
outside the hot path and counted in `gaps`. Any downstream fit or calibration window that crosses a gap is
**ineligible**, never silently complete.

- **Timebase (F3):** every queued envelope carries `event_at`: `decided_at` for a signal, `created_at` for a shadow,
  `closed_at` for an outcome. Gap bounds are inclusive UTC `event_at`.
- **Pending gaps:** a drop sets a pending gap, retried and flushed before any later record. A failed flush blocks writer
  output (`gap_flush_failed`), never trading.
- **Writer sessions:** each record carries `writer_id` and a monotonic `seq`, and the manifest has start and clean-close
  markers. A writer with no clean close creates an implicit open-ended gap from its last durable event through the next
  writer's first event. Gaps are never merged across writer IDs.
- **Window axis (G1):** training / calibration membership is keyed to the owning signal's `decided_at`, never an
  outcome's `closed_at`. Every queued outcome carries `signal_id` and the owning `decided_at`. A dropped outcome opens a
  gap against that signal, and the signal is ineligible even when its close lies outside the fit window. If ownership or
  decision time cannot be proven for a dropped record, the writer session is conservatively incomplete. A lot that only
  lacks `ml_decided_at` is signal-scoped (D1).
- **Coverage (G2):** every interval from one durable clean close to the next durable start is a gap (`coverage_gaps`).
  So are producer-disabled, flag-off, manifest-invalid, startup-failed and stopped intervals; they are not counters only.
  A zero-event interval is eligible only when an enabled writer session durably covers it.
- **Half-open windows:** a window is `[start, end)`, so a signal with `decided_at == end` belongs to the next window
  only.
- **Endcaps:** the manifest records every session start, clean close, disable marker and heartbeat (at most 60 s apart
  while enabled). Time before the first durable start, after the last clean close, and after an open session's last
  durable heartbeat is uncovered. A window is eligible only when its whole `[start, end)` is continuously covered.
  Flag-off, startup-failed and stopped intervals need no record of their own, because any uncovered time is a gap;
  producer_disabled and manifest-invalid write an explicit disable marker that ends coverage.
- **Heartbeats (C1):** emitted on a monotonic 60 s schedule and persisted with strictly increasing UTC timestamps.
  Coverage is the union of consecutive half-open heartbeat spans whose wall-time gap is at most **75 s** (60 + 15
  tolerance). A larger gap is an uncovered hole. A backward or non-increasing UTC step ends trustworthy coverage and
  opens a hole until a new durable session starts.
- **Whole-window eligibility (C2):** a window is eligible only when `_covered([start, end))` holds continuously over
  the whole window. Checking only the signal's timestamp is forbidden. Session and heartbeat spans are half-open `[a, b)`.
  Coverage spans come from heartbeats (C1), never from clean start / close alone, so a clean-close session with a
  heartbeat hole fails whole-window eligibility.
- **Sequence and closure (G3):** `seq` is allocated at the enqueue attempt, before the non-blocking offer, so a drop
  consumes a seq and the pending gap records it with the owning signal / decision context. A clean-close marker is
  forbidden until the queue is drained and no pending or unflushed gap exists. The start marker is durable before the
  first accepted record; a start-write failure disables the session and makes it unclean.

### 6b. Rotation prerequisite (R6)

Cowork measured about 1.06 s to fold 20k duplicate rows. **A runtime producer may not be enabled** until bounded,
append-only segmentation / rotation, with a manifest of segment hashes and idempotent replay across segments, is
implemented and tested. This is part of AI-SCAN-03 or a required predecessor, never post-producer cleanup.

- **Live enable check (F4):** a hash-valid manifest and a configured segment bound, verified at startup and on every
  hook call. A mismatch disables the producer (`producer_disabled`); it never appends on.
- **Hot path (G4):** the per-hook check reads one atomic, cached eligibility generation only: no disk I/O, hashing or
  manifest traversal on the trading thread. Startup / writer-side refresh verifies the manifest, hash and config and
  publishes the generation atomically. `segment_bound` is a positive integer within frozen byte / segment limits, never
  a truthy flag. **Frozen limits:** 1 MiB <= segment <= 16 MiB, at most 4 segments per read and at most 4096 manifest
  segments. One 16 MiB segment holds roughly 10k rows, which folds in about 0.5 s given Cowork's 1.06 s per 20k rows.
- **Manifest full (L1):** at 4096 manifest segments the producer disables before append and records
  `producer_disabled` / `manifest_full`.
- **Pagination (L1):** a window needing more than 4 segments is processed only by verified manifest pagination: each
  read covers at most 4 segments with hash-chain / cursor continuity, and any missing or changed page makes the window
  incomplete. The UI / API never returns a plausible partial row. The reader binds the expected page count, terminal
  cursor and final manifest end hash from the verified manifest first, so a valid truncated prefix, a missing middle
  page or a missing final page is incomplete.
- **Measurement (L1):** before runtime implementation, representative serialized signal / outcome / upgrade rows are
  measured and fold latency and resource evidence is recorded. The confirmed limits remain conservative v1 caps.
- **Bounded, causal reads:** the API reads manifest-bounded segments within a hard byte / segment bound and shows only
  causally complete views. When completeness cannot be proven, it returns a bounded incomplete / error result, never a
  partial plausible row.

### 6c. Fitting (F5)

No fitting or calibration consumer may use AI-SCAN rows until the separate ML restatement policy is frozen. After
that, a consumer selects exactly one latest valid measured revision per `signal_id`, after the gap and code checks.
Audit keeps every revision. **Latest means latest (G5):** the highest `rev` is selected first; if it is identity- or
cost-incomplete, the signal is excluded, and the fitter never falls back to an older valid revision.

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
- `test_v2_restatement_revert_appends_and_latest_is_rev2`
- `test_other_build_replay_is_noop_not_conflict`
- `test_drop_crash_restart_window_ineligible`
- `test_manifest_mismatch_disables_producer`
- `test_api_view_bounded_and_causally_complete`
- `test_one_trade_one_training_row`
- `test_dropped_outcome_makes_owning_signal_ineligible_by_decided_at`
- `test_uncovered_and_disabled_intervals_are_gaps`
- `test_sequence_allocated_at_enqueue_and_drop_records_it`
- `test_clean_close_forbidden_with_pending_gap`
- `test_hook_reads_cached_generation_only`
- `test_invalid_latest_revision_excludes_signal_no_fallback`
- `test_identity_upgrade_record_and_conflict`
- `test_close_hooks_carry_owning_decided_at_from_the_lot`
- `test_identity_upgrade_v2_replay_conflict_and_fold`
- `test_window_requires_continuous_coverage_including_endcaps`
- `test_window_boundary_is_half_open`
- `test_segment_bound_numeric_limits`
- `test_upgrade_replay_with_changed_transport_is_noop`
- `test_read_side_conflict_poisons_signal_never_raises`
- `test_upgrade_with_original_outside_page_is_unresolved`
- `test_heartbeat_hole_over_75s_and_backward_step`
- `test_whole_window_eligibility_and_t_equals_close`
- `test_missing_or_mismatched_decided_at_is_signal_scoped`
- `test_manifest_full_disables_before_append_and_pagination_continuity`
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

Follow-up rulings, Codex 6461183917 on Cowork 6461086863:

- **F1 revert:** integer `rev` is in the schema-v2 measured record key.
- **F2 cross-build:** `content_hash` excludes only `code` and `record_hash`; an other-build replay is a no-op.
- **F3 gaps:** `event_at` timebase, inclusive UTC bounds, pending gaps flushed first, writer_id + seq + clean close.
- **F4 rotation:** a live enable check, disable on mismatch, bounded causally complete reads.
- **F5 fitting:** none before the ML restatement policy is frozen; then one latest valid revision per signal.

Completeness rulings, Codex 6462174084 on Cowork 6461915411:

- **G1 window axis:** membership keyed to the owning signal's `decided_at`; a dropped outcome makes its signal ineligible.
- **G2 coverage:** clean close → next start, and every disabled / failed / stopped interval, is a gap.
- **G3 sequence:** seq at the enqueue attempt; no clean close with a pending gap; durable start marker first.
- **G4 hot path:** a cached atomic eligibility generation only; `segment_bound` a positive integer within frozen limits.
- **G5 latest:** highest rev first; an invalid latest excludes the signal, with no fallback.
- **G6 identity upgrade:** an explicit `identity_upgrade` record on an equal payload; a mismatch is a conflict.

Implementability rulings, Codex 6462613229 (stall-takeover review):

- **H1:** `lot['ml_decided_at']` is stamped at `_ml_link`; both close hooks carry `signal_id` + `decided_at` from memory.
- **H2:** an explicit v2 `identity_upgrade` kind with fields, key, replay / conflict and fold rule.
- **H3:** manifest start / close / disable / heartbeat markers; continuous `[start, end)` coverage required.
- **H4:** windows are half-open `[start, end)`.
- **H5:** frozen numeric segment limits.

Bounded-correction rulings, Codex 6462855816 on Cowork 6462804529:

- **U1:** semantic upgrade replay via `upgrade_content_hash`; transport fields never make a conflict.
- **U2:** read-side conflicts poison one signal and never raise; an original outside the page is `unresolved_original`.
- **C1:** 60 s monotonic heartbeats; gaps over 75 s and backward UTC steps are holes.
- **C2:** whole-window `[start, end)` coverage; half-open spans.
- **D1:** missing `decided_at` is signal-scoped; canonical six-digit microseconds `Z`; mismatches counted.
- **L1:** measure rows before runtime; `manifest_full` disables before append; verified pagination of at most 4 segments.
