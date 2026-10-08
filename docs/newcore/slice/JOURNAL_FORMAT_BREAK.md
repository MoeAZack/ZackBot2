# Pre-freeze journals: an explicit pre-release format break

Status: accepted by Codex (review #13 of facd4b6). This is not a migration plan; **there is no migration**.

## What changed

NC-01 (master dfd6b03) froze the domain records. `OrderIntent` now requires `owner_kind` and
`replaces_intent_id`, and the store decodes every event with the strict schema-1 codec (`newcore/domain/codec.py`).
A journal written before the freeze has intent records without those keys.

## Behaviour (fail-closed)

1. The codec rejects such a record: `decode_result` -> `INVALID`
   (`... intents[0]: missing keys ['replaces_intent_id']`).
2. Store recovery (`newcore/store/recovery.py`) returns **DAMAGED** for the journal. It never skips the record, never
   truncates, and never rewrites the bytes.
3. `python -m newcore.run` refuses the store (HOLD, `EXIT_STORE_HOLD`) and runs as the **tail-loss guard** instead:
   - it opens nothing new;
   - it protects only exposure it can prove is its own from venue facts (the surviving net of its own orders, Codex
     P1-1), with a degraded emergency stop or, if no stop can be confirmed, the reduce-only emergency close;
   - it touches nothing ambiguous or foreign (HOLD + an incident).
4. The journal directory is left byte-identical.

## What is deliberately NOT done

- There is no migration of old records to the new schema.
- There is no compatibility shim that guesses `replaces_intent_id` (or `owner_kind`) for an old record. A guessed
  lineage would be a reinterpretation of history, which the journal contract forbids.
- There is no schema/version bump to read schema-0 records. If one is ever needed, it belongs to the NC-01 / NC-02
  lane, not the runner.

## Operator procedure

Pre-freeze journals only ever existed in pre-release PAPER / testnet runs.

1. Stop the bot. If the venue shows exposure, the guard protects provable own exposure; anything it reports as
   ambiguous needs the operator.
2. Flatten or adopt the positions manually on the venue (testnet).
3. Archive the old account journal directory (move it, do not delete it).
4. Start fresh. An empty journal with venue exposure routes to the guard again, so start flat.

## Tests

- `tests/newcore_slice/test_prefreeze_journal_break.py`:
  - the strict codec rejects a stripped intent record;
  - a real durable journal rewritten in the pre-freeze shape (valid frames and CRCs, only the schema differs) boots
    to DAMAGED -> GUARD, places no entry, protects the own lot, and leaves the segments byte-identical.
- `tests/newcore_slice/test_journal_compat.py`: compatibility the slice *does* keep (v2/v3 management ticks, pre-N6
  close order). Those are runner detail strings inside schema-1 records, not domain schema changes.
