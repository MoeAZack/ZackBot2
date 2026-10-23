# AI-SCAN-01 — causal AI chart scanner and calibrated shadow dashboard (contract, #199)

## Roadmap fit

Phase 10 (ML in shadow). This extends TA-SCOUT-01 and supports EDGE-01. It is PAPER, shadow and report-only. It is ZackBot-owned and
does not depend on TradingView; webhook ingestion stays Phase 11 TV-01.

## Status of this document

This is the contract stage. `ai_scan.py` holds the immutable record schema and the pure replay. `tests/test_ai_scan.py` pins it. Nothing is
wired into the engine or app yet. Implementation order after the two #187 packets: ledger → scanner API/UI →
calibration/evaluation harness.

## Records (`ai-scan-schema-v1`, append-only JSONL, one sealed `record_hash` each)

| kind | key | written when | content |
| --- | --- | --- | --- |
| `signal` | `signal_id` | the engine has decided (same point as the ML-SHADOW decision hook) | cutoff, snapshot hash, deterministic plan, rules status/reason, cost estimate, input coverage, `OK`/`INSUFFICIENT` + reasons |
| `shadow` | `signal_id` + model + prompt version | asynchronously, after the cutoff | opinion `LONG`/`SHORT`/`NO_TRADE`, coverage, disagreement, probability state, bounded rationale, the snapshot/plan hashes it read |
| `outcome` | `signal_id` + `rev` | at close (rev 0 = booked), and on each actual-cost revision (rev n) | net R / PnL, exit reason, cost basis `booked`/`measured`, partial exits, adds |

- **`signal_id`** = `ml_shadow.ledger.decision_id(sleeve, strategy, symbol, side, tf, candle_open)`. It is deterministic, so a restart re-derives
  the same id, and it joins the existing ML-SHADOW decision and label rows.
- **Cutoff** = the decision candle's close. `candle_open` is the engine's `sg['time']`, which is the decision candle's open
  (`engine.py`, `dec_open`). That is measured, not assumed.
- **Plan, rules and costs** are copied from the deterministic engine snapshot. The shadow layer never writes them.

## Fail-closed rules (all pinned)

- **INSUFFICIENT inputs.** The following give `INSUFFICIENT` with the exact reason, never an inferred score:
  - a missing snapshot, features or last close;
  - a closed candle after the cutoff;
  - a snapshot older than 2 bars;
  - a non-finite or non-numeric feature;
  - a missing or malformed plan or targets;
  - a stop or target on the wrong side;
  - an unknown side or tf.

  A `None` feature only lowers coverage.
- **No shadow on INSUFFICIENT.** An `INSUFFICIENT` signal gets no shadow record.
- **Shadow refusals.** A shadow record is refused in each of these cases:
  - it was created before the cutoff;
  - it read a different snapshot or plan hash;
  - it carries any plan, sizing, order, exit, risk, settings, eligibility or rotation field.
- **Probability.** It is kept only with a calibration marked `passed` and carrying an id. Otherwise it is dropped and the state is
  `UNVERIFIED`.
- **Outcomes.** An outcome is refused if it closed at or before the cutoff, or if it sits on a `skipped` decision. A higher `rev` supersedes.
- **Replay (`fold`).**
  - An exact duplicate is a no-op, which is what makes restarts idempotent.
  - A conflicting replay of the same key, a join to an unrecorded signal, an edited record (hash mismatch) or an unknown schema
    all raise `AIScanError`.
- **No secrets.** Features, plan and costs may not carry credential-like or exchange/order/client/trade-id fields.

## API contract (report-only; flag `AI_SCAN`, default OFF, PAPER only)

| method | path | response |
| --- | --- | --- |
| GET | `/api/ai_scan` | `{rev, on, rows:[display(view)...], health}`, newest first. Optional `?sort=` with `age`/`symbol`/`coverage`/`disagreement`/`outcome_r` and `?limit<=500` |
| GET | `/api/ai_scan/<signal_id>` | one `display(view)` plus rationale, entry/stop/targets, the cost estimate, age and the outcome revision history |

- **Flag off.** Both paths return `{on:false, rows:[]}` when the flag is off; no ledger is read. There is no POST, PUT or DELETE.
- **Labels.** The labels never merge:
  - `RULES` is the deterministic decision and plan;
  - `AI SHADOW` is one row per model;
  - `UNVERIFIED` is shown in place of any uncalibrated probability;
  - `INSUFFICIENT` is shown with its reasons.
- **Never exposed:** credentials, exchange or order ids, raw logs, raw state.

## Evaluation (preregistered, harness stage)

B0 is the current deterministic baseline and B1 is the scanner ranking only. Both run on the same tape and the same fee, funding and fill
model, anchored walk-forward plus the untouched holdout. The harness reports trades/week, retained-trade rate, net expectancy, PF, DD,
tail loss, costs/R, long/short, regime/coin concentration, Brier score, calibration error, coverage and latency. Forward PAPER confirms
transfer, but 2–3 days never promotes.

## Deferred

- reverse-engineered TradingView clients;
- TV webhook ingestion (Phase 11);
- live/news authority, execution, veto or risk authority, leverage or risk above 1x;
- holdout parameter mining.
