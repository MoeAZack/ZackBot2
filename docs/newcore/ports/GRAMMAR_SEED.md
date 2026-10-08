# Step-0 journal: the grammar seed (contract DRAFT for Codex review)

Status: **DRAFT r1, contract only, no implementation on this branch yet.** Codex confirmed the approach on #13
(6069341639). This file is for reviewing the CONTRACT before the code lands on `nc-ports-gate-seed`.
**Compaction stays DISABLED** until the parity suite below and Cowork's attack pass are green.

## 1. Problem

`JournalGate.rebuild(account, aggregate, tail, facts=, after_sequence=k)` seeds only the durable facts (NC-01 FactIndex)
and the sequence. The step-0 grammar also needs the decisions, intents, lots and lineage the compacted prefix created.
So any tail that refers to that prefix is refused: G5 "intent recorded before its decision", G7-G10 "... before the
intent was recorded", "owner is no lot opened by a recorded entry fill". On the store's 18-event scenario every cut
k = 1..17 fails. `check_event_chain(known_intents=)` has a seed on the domain side; the gate has none.

## 2. The type

`newcore/ports/seed.py` adds `GrammarSeed`, an immutable NC-01 `Record` (frozen, slotted, kw-only, no defaults). The
codec checks every field. It is the complete gate state after a prefix of the journal, and nothing else:

| field | type | what it carries (gate attribute it restores) |
|---|---|---|
| `seed_version` | int | `GRAMMAR_SEED_VERSION` (= 1). Any other value FAILS CLOSED. |
| `account_id`, `aggregate_id` | ids | G1 (must equal the gate's) |
| `last_sequence` | int >= 0 | `Grammar.last_sequence` (G2) |
| `events` | tuple[EventDigest] | G3: every admitted event id with its sequence and canonical digest, sequences 1..last_sequence exactly |
| `decisions` | tuple[SeedDecision] | G4: `decision_id`, `key: DecisionKey \| None`, `authorized: tuple[int_ id]` |
| `intents` | tuple[SeedIntent] | G5-G10 + the gate's NC-01 content state, one per recorded intent (live AND terminal) |
| `lots` | tuple[lot_ id] | lots opened by a recorded entry fill (G5 lineage owners) |
| `lineage` | tuple[SeedLineage] | `(owner_id, purpose, next_ordinal)`: derive_child_intent_id ordinals |
| `last_protect` | tuple[SeedProtect] | `(owner_id, intent_id)`: the G6 classic -> algo fallback |
| `facts` | FactIndex | the NC-01 durable facts (results, incidents, consumed venue trades) of the SAME prefix |

`SeedIntent` = `intent: OrderIntent` (the durable record as journaled), `state: IntentState` (current lifecycle state),
`sent_at_ms: int | None` (the SUBMITTED step, G7), `final: OrderResult | None` (its FINAL result, the superseding one
after a late fill), `superseded: bool` (r3 3b), `late_result_id: res_ | None` and `late_applied: bool` (r3 3b
exactly-once). The grammar's client-id set and route are DERIVED from `intent` (`client_order_id`, `route_of`), not
stored twice. Every tuple is in canonical order (by id / key), so one gate state has one byte form.

## 3. Derivation (the only producer)

`JournalGate.grammar_seed() -> GrammarSeed` exports the state of a gate that admitted the durable journal through
`last_sequence`. NC-02 writes it in the same atomic step as the Snapshot of that sequence and stores it beside it.
Nobody builds a seed by hand or from parallel lists. The only consumer is
`JournalGate.rebuild(account, aggregate, tail, grammar_seed=seed)`.

## 4. Fail-closed rules (each raises a typed error; the gate is never built half-seeded)

1. `seed_version != GRAMMAR_SEED_VERSION` -> refused (unsupported version, no migration).
2. Another account / aggregate than the rebuild's -> refused.
3. Prefix coverage: `events` holds exactly sequences 1..`last_sequence` (no gap, no duplicate event id), and against the
   snapshot it is restored with, `seed.last_sequence == snapshot.last_sequence` and `seed.facts == snapshot.facts`
   (`check_seed_for_snapshot(seed, snapshot)`). Any mismatch -> refused.
4. Owner / lifecycle completeness: every intent's decision is in `decisions` and authorizes it; every lot-owned intent's
   owner is in `lots`, and every entry-owned intent's owner is a seeded ENTRY intent; `lineage` ordinals cover each
   (owner, purpose) group; client ids are unique.
5. Impossible state -> refused: a `state` the intent's purpose / post-hoc rule cannot reach; `sent_at_ms` set without a
   sent step (or missing on a sent state); a terminal `state` that is not `terminal_for(final)`; `final` that is not
   FINAL, does not belong to the intent (check_result_for_intent), or exists for a still-sent-free intent wrongly;
   `superseded` without a superseding final; `late_applied` without `late_result_id`; facts that do not contain a seeded
   final result's id.
6. `grammar_seed=` together with `facts=` / `after_sequence=` -> refused: the seed is the one source.

## 5. Parity (the acceptance suite; compaction stays off until it and Cowork's attacks pass)

For a multi-event scenario covering live and terminal intents, owner lots, entry-owned stops, the algo fallback,
UNKNOWN / KNOWN / FINAL results, a late fill after not-found + its reconcile, an external close booking with venue
trades, incidents and an empty tail, for EVERY cut k = 0..N:

- `rebuild(events[:k]).grammar_seed()` survives a byte round trip, and
  `rebuild(events[k:], grammar_seed=seed).grammar_seed() == rebuild(events).grammar_seed()` (same gate state);
- the same probe events get the same accept / refuse answer from the restored gate and from the full-log gate: reused
  decision / intent / client / result / venue-trade / incident ids, an event id re-used with other bytes, invalid
  lifecycle transitions, results after the final one, and the valid next event.

## 6. Boundaries

- Ports-only: no filesystem, store or snapshot I/O in `JournalGate`. NC-02 owns where the seed is written and read.
- No change to the NC-01 domain types. The seed is built from them (OrderIntent, OrderResult, DecisionKey, EventDigest,
  FactIndex).
- `facts=` / `after_sequence=` stay for the full-log + facts case; with a seed they are refused (rule 6).

## 7. Open for Codex

- `events` (G3 digests) grows with the journal. Keep them all in v1 (exact G3 parity), or keep only the latest N and
  accept that a re-delivered compacted event then becomes a conflict instead of a no-op?
- Should the seed also carry the domain chain's `known_intents` form, so `check_event_chain` and the gate restore from
  one object? (Proposed: yes - `SeedIntent` already holds `(intent, state, sent_at_ms)`.)
