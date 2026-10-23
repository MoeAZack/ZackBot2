# AI-SCAN-01 — signal-ledger schema and API detail (#199)

The owner-facing contract is `docs/AI_SCAN_01.md` (#200, Integration-owned). This file is the Build-side record and API detail
it is implemented against. `ai_scan.py` holds the immutable record schema and the pure replay, and `tests/test_ai_scan.py` pins
them. Nothing is wired into the engine or app yet. Implementation order after the two #187 packets: ledger → scanner API/UI →
calibration/evaluation harness.

## Records (`ai-scan-schema-v1`, append-only JSONL, one sealed `record_hash` each)

| kind | replay key | written when | content |
| --- | --- | --- | --- |
| `signal` | `signal_id` | the engine has decided (same point as the ML-SHADOW decision hook) | candle open, cutoff, decision instant, input availability times, snapshot hash, deterministic plan, rules status/reason, cost estimate, input coverage, `OK`/`INSUFFICIENT` + reasons |
| `shadow` | `signal_id` + `epoch` | asynchronously, after the decision | pinned `model_id`, `prompt_hash`, `sampling`, `raw_response_hash`, the epoch, opinion `LONG`/`SHORT`/`NO_TRADE`, coverage, disagreement, probability block, bounded rationale, the snapshot/plan hashes it read |
| `outcome` | `signal_id` + `source` + `rev` | measured: at close (rev 0 = booked) and on each actual-cost revision; simulated: from the causal replay | net R / PnL, exit reason, cost basis, partial exits, adds |

- **`signal_id`** is `ml_shadow.ledger.decision_id(sleeve, strategy, symbol, side, tf, candle_open)`. It is deterministic and
  restart-stable, and it joins the ML-SHADOW decision and label rows.
- **Time semantics (Cowork #200 F5):**
  - `candle_open` is the engine's `sg['time']`. Measured: it is the decision candle's open (`engine.py` `dec_open`).
  - `cutoff` is its close.
  - `decided_at` is the engine's decision instant, which is ≥ the cutoff.
  - Every causal input must have been **available (ingested)** by `decided_at`: the snapshot (`available_at`), `funding`,
    point-in-time `universe` membership and `exchange_rules`. A late or missing availability time makes the signal
    `INSUFFICIENT` (`after_decision:<input>` / `missing:<input>`), so restated or later-listed data cannot leak.
- **Model reproducibility (F3):** `epoch = sha256(model_id, prompt_hash, sampling)[:16]`. A change of any of the three opens a new
  epoch. A calibration applies only to its own epoch, so a silently re-pointed alias cannot inherit one.
- **Probability (F1/F2):**
  - A probability is kept only when the calibration is `passed`, carries an `id`, is of the same `epoch`, and declares a
    `target` = {`label`, `horizon_bars`, `cost_basis`}, i.e. what it is a probability *of*. Otherwise it is dropped, and the state
    is `UNVERIFIED`.
  - The pass bar (minimum n per bin, ECE/Brier vs the B0 base rate, a calibration window disjoint from evaluation, demotion on
    drift) is the harness's verdict. It is recorded as `passed`, and its thresholds belong in the #200 contract.
- **Outcomes (F4):**
  - `measured` is the engine's own trade, with cost basis `booked` or `measured`.
  - `simulated` is a causal replay on the same cost model, with cost basis `modelled`.
  - A skipped or untraded candidate can only get a simulated outcome.
  - The two sources are replayed, displayed and reported separately and are never merged, so B1 is not scored on a
    survivor-biased subset.

## Fail-closed rules (all pinned)

- **INSUFFICIENT inputs.** Each of these gives `INSUFFICIENT` with the exact reason, never an inferred score:
  - a missing snapshot, features or last close;
  - a closed candle after the cutoff;
  - a snapshot older than 2 bars;
  - an input available after the decision instant;
  - a non-finite or non-numeric feature;
  - a missing or malformed plan or targets;
  - a stop or target on the wrong side;
  - an unknown side or tf.

  A `None` feature only lowers coverage.
- **Signal refusals.** A signal is refused if it was decided before its candle closed. An `INSUFFICIENT` signal gets no shadow
  record.
- **Shadow refusals.** A shadow record is refused in each of these cases:
  - it was created before the decision;
  - it lacks a pinned model id, sampling parameters or sha256 prompt/response hashes;
  - it read a different snapshot or plan hash;
  - it carries any plan, sizing, order, exit, risk, settings, eligibility or rotation field.
- **Outcome refusals.** An outcome is refused in each of these cases:
  - it closed at or before the decision;
  - it is a measured outcome on a skipped decision;
  - its cost basis doesn't match its source.

  A higher `rev` supersedes.
- **Replay (`fold`).**
  - An exact duplicate is a no-op, which is what makes restarts idempotent.
  - A conflicting replay of the same key, a join to an unrecorded signal, an edited or forged record, or an unknown schema all
    raise `AIScanError`.
- **No secrets.** Features, plan and costs may not carry credential-like or exchange/order/client/trade-id fields.

## API (report-only; flag `AI_SCAN`, default OFF, PAPER only)

| method | path | response |
| --- | --- | --- |
| GET | `/api/ai_scan` | `{rev, on, rows:[display(view)...], health}`, newest first. Optional `?sort=` with `age`/`symbol`/`coverage`/`disagreement`/`outcome_r` and `?limit<=500` |
| GET | `/api/ai_scan/<signal_id>` | one `display(view)` plus rationale, entry/stop/targets, the cost estimate, age, and the outcome revision history per source |

- **Flag off.** Both paths return `{on:false, rows:[]}` when the flag is off; no ledger is read. There is no POST, PUT or DELETE.
- **Labels.** The labels never merge:
  - `RULES` is the deterministic decision and plan;
  - `AI SHADOW` is one row per model epoch;
  - `UNVERIFIED` is shown in place of any uncalibrated probability; a calibrated one is shown with its target;
  - `INSUFFICIENT` is shown with its reasons.
- **Never exposed:** credentials, exchange or order ids, raw logs, raw state.

## Owned by the #200 contract (not decided here)

- the probability target's concrete label and horizon;
- the calibration pass-bar thresholds;
- which holdout, who seals it and when (never an existing EDGE/newcore sealed holdout);
- the hashed B0/B1 preregistration (primary endpoint, minimum n, multiple-comparison treatment);
- the remote-hang/timeout/queue-full test (bounded queue + spend budget) and the holdout-unreadable test (Cowork F6). Both land
  with the queue and harness stages.
