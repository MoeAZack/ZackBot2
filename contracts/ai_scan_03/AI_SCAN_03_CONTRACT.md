# AI-SCAN-03 — writer hook: immutable decisions joined to measured cost revisions (PAPER / shadow only)

**Status: DRAFT — unsigned.** This is a contract only. It adds no runtime code and calls no model. The machine twin
`ai_scan_03_contract.v1.json` sits next to this file and is authoritative. Tests read the JSON and check this file
against it. Signing sets `status` → `FROZEN`, plus `signed_by` and `frozen_at`. Assignment: Codex 6460083994 on #222.
It is built from `origin/master` @ `fe84dbb`. For reference only, it also reads #222 (`ai-scan-02` @ `64ef92c`,
`ai_scan_ledger.py`) and #226 (`claude/ml-pair-01` @ `c77c756`, `ml_shadow/pairs.py`). This slice does not edit or
import either of them.

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
slice widens that stamp to fire when ML_SHADOW **or** AI_SCAN is on. A lot without the id produces no outcome
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
  closes never do. The hook passes net basis `measured` only.
- **Cost identity:** `ai_scan.cost_revision_id`, schema `ai-scan-cost-rev-v2`, over
  `fees_actual, funding_actual, pnl_gross, pnl_fill_adj, risk_usd`. Net is
  `pnl_gross + pnl_fill_adj - fees_actual + funding_actual`, and net R is that net divided by `risk_usd`. The cost model
  id is `actual_costs.MEASURED_COST_MODEL_ID`, whose single definition arrives with #226.
- **Consistency with ML-PAIR-01 (#226):** its `pair_key = (decision_id, cost_rev_id)` maps one-to-one onto
  `(signal_id, cost_revision_id)`. Both use the same cost identity and the same close gate. Nothing here imports from an
  unmerged branch. Once #226 merges, `test_cost_identity_matches_ml_pair_01` pins the two equal.

## 4. Never gates orders

The engine-thread hook only builds a sealed record inside `try/except`. It then offers the record to a **bounded,
non-blocking queue**. An offer never blocks and never raises; a full queue drops the record and counts `queue_full`.

A dedicated **writer daemon thread** owns the scan ledger and does every disk write. It never holds the engine lock
and never reads engine state. Each ledger has a single writer.

Every exception in the hook or the writer is counted and swallowed, so none reaches order flow. The flag is read at
each call as `S.get('AI_SCAN') is True`.

Proof: `test_trading_byte_identical_with_ai_scan_on_off_and_failing_writer`. It replays the same PAPER cycle four
ways: with AI_SCAN off, on, on with a raising writer, and on with a full queue. Orders, history, state and order calls
must be byte-identical across all four.

## 5. Identity

Each record carries `code = {build_id, source_commit, bundle_hash}`. This needs `ai-scan-schema-v2`, and
`record_hash` covers `code`. The completeness rule is #226's `identity_gap`: a non-dev build id, a 40-hex source
commit from the matching build.json, and a 64-hex bundle hash. The bundle adds `ai_scan.py` and `ai_scan_ledger.py`.

When the identity is incomplete (reason `dev_build`, `source_commit` or `bundle_hash`):
- **signal:** written with `code_complete false`. It is never eligible for fitting, calibration or pairing.
- **shadow:** written with `code_complete false`. It never verifies.
- **outcome:** refused, nothing written. The hook counts `identity_incomplete` and records the identity reason.

## 6. Failure modes and counters (`health['ai_scan']`)

`signals`, `shadows`, `outcomes`, `replays`, `conflicts`, `queue_full`, `refused_live`, `not_taken`,
`non_measured_close`, `cost_incomplete`, `no_decision_id`, `unknown_signal`, `ambiguous_rev`, `identity_incomplete`,
`build_errors`, `write_errors`, `skipped_lines`.

When the flag is off, the hook returns before building anything and the ledger is untouched. A torn or edited line is
skipped on read and never raises. A disk error is swallowed, and the next record is tried.

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
- `test_api_view_unchanged_shape`

The JSON twin states what each one asserts.

## 8. DEFERRED

`runtime_producer_hook`, `remote_model_api_call`, `ledger_rotation`, `model_promotion`, `ui_controls`,
`trading_authority`, `simulated_outcomes`.

## 9. Open questions for Codex

1. Should the lot's decision-id stamp widen to ML_SHADOW **or** AI_SCAN, or should AI_SCAN require ML_SHADOW to be on?
2. `ai_scan.COST_BASES['measured']` still admits net basis `booked`. Should the runtime slice narrow it? Today the hook
   does the filtering.
3. ML-PAIR-01 pairs only at the first measured close. Should it also pair restatements from `_row_changed`, so that
   both stores carry the same revisions?
4. Identity on each record needs `ai-scan-schema-v2`. Is that acceptable, or should identity live in a once-written
   epoch file, as `pair_epoch.json` does?
5. Should AI-SCAN have a dedicated writer thread, or a new row kind on the existing `ml_shadow` LedgerWriter?
