# NEWCORE step 0: the shared interface frozen before parallel slice work

**State:** r3 for Codex re-review (PR #37), 08 Oct 2026, Africa/Cairo. r3 adds the staged journal admission (store atomicity).
- **Branch:** `master` @ `ccd9601` merged with NC-01 `nc-01-domain` @ `cd721c5` (PR #38). This branch is stacked on
  NC-01.
- **Code:** `newcore/ports/`: Protocols, the one journal gate and small value types; no adapters.
- **Tests:** `tests/newcore_ports/`, including the reusable JournalPort contract suite.
- **Ruling implemented:**
  - a canonical decision key;
  - a restart-stable intent id and the consumed-signal rule;
  - one JournalPort event grammar;
  - a fixed VenuePort and a separate BarSource.

**Binding to NC-01:** `newcore.ports` imports only the stdlib and `newcore.domain`. Every NC-01 type and value rule
comes from NC-01 itself; ports keep no mirror copies. These are used directly:
- `DecisionKey`, `Side`, `Purpose`, `IntentState`, `Evidence`, `Admission`;
- the id, ms, symbol, client-id and Decimal validators;
- `canonical_bytes` / `contract_sha256`.

## 1. DecisionKey: NC-01's record, built by one constructor

`keys.decision_key(name, version, tf, symbol, side, candle_close_ms, purpose)` is the **only runner-boundary
constructor**. The grammar refuses a keyed decision whose key it did not build.

| Field | Rule | Example |
|---|---|---|
| `strategy` | `strategy_instance(name, tf)`: `[a-z0-9_]{1,24}@<tf>`, tf ∈ 1m…1d | `trend_ema_mom@4h` |
| `strategy_version` | canonical `v<n>`: no leading zeros (`v0`, `v1`, `v12`; `v00` and `v01` are refused) | `v1` |
| `symbol`, `side`, `purpose` | NC-01 rules and enums | `BTCUSDT`, `LONG`, `entry` |
| `candle_close_ms` | int UTC ms on the tf grid, = signal candle `open_ms + tf_ms` (= Binance `closeTime + 1`) | `1759924800000` |

- **The timeframe is part of the identity.** A 4h and a 15m key on the same close differ, and so do their ids (tested).
- **Bytes:** these are NC-01 `codec.canonical_bytes(key)`, and the pinned bytes are tested.
- **Strategy mapping:**
  - rule `trend_ema_mom.v1` → `decision_key('trend_ema_mom', 'v1', '<tf>', ...)`;
  - `signal_time_ms` → `candle_close_ms`;
  - ENTER/CLOSE → `entry`/`close`.

## 2. Restart-stable ids and the consumed signal

`H(tag, parts...) = sha256(b"zackbot.newcore.<tag>.v1" + b"\0" + part ...)`. Ids are `<prefix>_` plus the first 32 hex
characters. These are pure functions with pinned test vectors.

| Id | Derivation |
|---|---|
| `decision_id` | `dec_ H(decision_id, account, key_bytes)`: one decision per (account, key) |
| `intent_id` | `int_ H(intent_id, account, key_bytes, "0")`: **one** intent per keyed decision |
| `lot_id` | `lot_ H(lot_id, account, entry_intent_id)`: the lot an entry's fill opens |
| child `intent_id` | `int_ H(child_intent_id, account, owner_id, purpose, ordinal)` |
| `client_id` | `"zbn1" + ("o" classic \| "a" algo) + "-" + base32(H(client_id, intent_id, route))[:26]` |

**Child intents:**
- `owner_id` is the entry intent or the lot the intent works for; NC-01's `OrderIntent.owner_kind`
  (`entry_intent` / `lot`) says which. The journal resolves the owner by that kind, never by the id's prefix.
- The ordinal is the journal's count of earlier `(owner, purpose)` intents, read with
  `Grammar.next_child_intent_id`. The journal sets it, not the caller.

**Client ids:**
- 32 characters, Binance charset, never the legacy `z[ab]`+22 hex shape.
- Exactly one per intent; `route_of` recovers the route.

**Collisions:** a sample of 102,400 keyed derivations has no collision across decision, intent and lot ids or client ids.

**Consumed signal:**
- A key is consumed once its `decision_recorded` is durable. That includes a SKIP decision with no intents.
- `claim_signal` answers a re-delivered signal with `fresh=False`, the same decision id and the intents already
  recorded. It never gives new ones.
- A rejected or not-sent keyed intent is not retried under the same key.

## 3. JournalPort, header_of, and the one gate

**JournalPort methods:**
- `append(event) -> Admission` follows a fixed store-atomicity order (r3):
  1. `staged = gate().stage(event)`: validate only. It raises `JournalConflict`, and `None` means `ALREADY_APPLIED`
     (write nothing).
  2. Write and fsync the event. On failure, raise `JournalUnavailable` and drop `staged`. Nothing is durable and the
     gate is unchanged, so the same event can be appended later, exactly once.
  3. `staged.commit()` returns `APPLY`. Only now are the sequence, decision, intent and lineage consumed.
  - `StagedEvent.commit()` runs once and refuses if the gate moved after staging.
  - `JournalGate.admit` (stage plus commit) is only for events that are already durable, at rebuild or replay.
- `last_sequence()`, `read(after)`, `find_decision(id)`.
- `gate()`: the admission state that `claim_signal` and `next_child_intent_id` read.

**The admission chain:** `JournalGate.admit(event)` runs `header_of(event)`, the one canonical projection of an NC-01
DomainEvent. It then runs `Grammar` (G1–G10). Last come NC-01's per-record checks:
- `from_state` is the current state;
- `can_transition`;
- `check_result_for_intent`;
- the terminal step is `terminal_for(final result)`.

A refused event and a failed write both change nothing. After a restart, `JournalGate.rebuild(durable events)` restores
the state.

| Kind | NC-01 event | Header fields |
|---|---|---|
| `decision_recorded` | DecisionRecorded | `decision_id`, `decision_key`, `authorized` = ids of `Decision.intents` |
| `intent_recorded` | IntentRecorded | `decision_id`, `intent_id`, `purpose`, `owner_id`, `owner_kind`, `client_ids` (one) |
| `sent` / `state_changed` / `intent_closed` | IntentStateChanged → submitted / working·unknown·cancelling / terminal | `intent_id`, `to_state` |
| `result_recorded` | ResultObserved | `intent_id`, `outcome` final/known/unknown/not_found, `evidence`, the result's client id |
| `mode_changed` | ModeChanged | (HOLD in/out, halt; used by the slice) |
| `binding_changed` | BindingChanged | (the binding confirmation the risk gate requires) |

`incident_recorded` is deferred because NC-01 has no record type for it. `protection_*` is the protect-purpose intent's
own events.

**Grammar rules:**
- **G1:** one account and aggregate per journal.
- **G2:** sequence = last + 1.
- **G3:** the same `event_id` with the same sequence and digest is a no-op; anything else is a conflict.
- **G4, decisions:**
  - the decision id is new, and it commits its exact authorized set;
  - a keyed decision's key comes from `decision_key()`, its id is derived, and it authorizes `()` or exactly
    `derive_intent_id(account, key)`.
- **G5, intents:**
  - the intent is new, its decision was recorded earlier, and that decision authorized it;
  - a keyed intent has the key's purpose;
  - any other intent needs an owner and must have the derived lineage id (owner, purpose, journal ordinal);
  - an `entry_intent` owner is a recorded entry, and a `lot` owner is `derive_lot_id` of an entry with an
    **executing** final result (a `portfolio`-owned intent is cancel-only in NC-01, so it is never recorded new);
  - the client id is `client_id_for(intent, route)` and is never reused.
- **G6, routes:**
  - `algo` is allowed for protect only;
  - it must be the fallback of the owner's previous protect attempt, which was **sent on classic and closed
    rejected**;
  - it is never allowed after an unknown, never twice, and never without a classic attempt.
- **G7:** `sent` happens once, and only from recorded.
- **G8:** `working`/`unknown` only after `sent`, and nothing but the close after a final result.
- **G9:** no result before the intent or after the final one. The result names the intent's client id. A never-sent
  intent takes only `not_sent`.
- **G10:** close only after a final result; nothing follows it.

**Route fallback model: deterministic child attempt intents.**
- Each attempt is one intent, with one route and one send, and ends terminal after its route.
- The classic refusal (for example -4120) becomes FINAL `exchange_refused` and closes `rejected`. The algo attempt is
  then `next_child_intent_id(owner, protect)`, recorded and sent under a new id.
- A restart between the routes gives the same id.
- The protection itself has no terminal state. When routing is exhausted (classic then algo both rejected), the runner
  closes at market or goes to HOLD.

**Contract suite** (`tests/newcore_ports/journal_contract.py`, class `JournalContract`):
- An implementation subclasses it and provides three fixtures: `make_journal`, `reopen`, and `fail_next_write`. The
  last is the failure-injection hook: the journal's next durable write (write, fsync or replace) fails once. The test
  file must sit in `tests/newcore_ports/` so that `journal_contract` and `nc_events` can be imported.
- Store atomicity is tested at six failure points: the keyed decision, the entry intent, the fill, the protect lineage,
  the algo route and a result. At each one the suite proves that:
  - the durable stream is unchanged;
  - nothing is consumed: sequence, decision, lineage, signal claim or `find_decision`;
  - restart state equals in-process state;
  - the same event then appends exactly once and the flow completes.
  A failure, then a restart, then an append is covered too.
- Every event is a real NC-01 record built by `nc_events.Scenario`.
- It covers 4 good journals, each cross-checked with NC-01 `check_event_chain` and `ledger.admit`.
- It covers 27 refused events, and each asserts its exact refusal reason.
- It covers restart between routes and the consumed signal across a restart.
- `ReferenceJournal`, in memory, passes it.

## 4. VenuePort (unchanged)

| Call | Returns |
|---|---|
| `submit_market(MarketOrder(ref, position_side, qty, reduce))` / `submit_stop(StopOrder(ref, position_side, qty, stop_price))` | `OrderOutcome` |
| `cancel(OrderRef(symbol, client_id, route))` / `query(OrderRef)` | `OrderOutcome` |
| `positions()` / `open_orders()` / `fills(symbol, exchange_order_id)` | `ReadOutcome` |

- **Calls:** each call sends one request and never retries or switches route. The Runner journals each route as its
  own attempt intent.
- **Outcome kinds:** they match the transport's values one to one. Only `final` carries an executed quantity.
- **Reads:** an unknown read is never empty.

## 5. BarSource (unchanged)

`closed_bars(symbol, tf_ms, *, as_of_ms, limit) -> ReadOutcome[tuple[Bar]]`.
- Bars have Decimal OHLCV, `close_ms = open_ms + tf_ms`, and are aligned and contiguous.
- Every bar has `close_ms <= as_of_ms`. The forming candle is never returned.

## 6. Deltas S1 and NC-02a must absorb (r1 → r2)

1. **Keys:**
   - Build keys only with `keys.decision_key()`.
   - `newcore.ports.DecisionKey` is gone; use NC-01's.
   - `derive_intent_id(account, key)` has no `leg` argument.
2. **Decisions commit their intents:**
   - Every intent must be listed in its decision's `intents`.
   - A keyed ENTER or CLOSE has exactly the one derived intent.
   - A SKIP has none.
3. **Lineage:** protect, replacement, reduce and close intents that no key covers need:
   - an `owner_id` with its `owner_kind` (`entry_intent`, or `lot` = `derive_lot_id(account, entry_intent_id)`);
   - the id from `journal.gate().grammar.next_child_intent_id(owner, purpose)`, taken immediately before the decision.
4. **One client id per intent:**
   - NC-01 `alt_client_order_id` must be `None`.
   - The algo fallback is a new protect intent with `client_id_for(id, 'algo')`, allowed only after a sent classic
     attempt closed `rejected`.
5. **Journal implementations:**
   - S1's MemoryJournal and NC-02a's file journal expose `gate()` and pass `JournalContract`, including the new
     `fail_next_write` fixture.
   - **r3:** `append` must be `gate().stage(event)`, then write and fsync, then `staged.commit()`. Never call
     `gate().admit()` before the write.
   - NC-02a rebuilds the gate with `JournalGate.rebuild` from the durable events at boot.
   - A failed write raises `JournalUnavailable` with nothing durable and nothing consumed. A retry of the same event
     must then append exactly once, so NC-02a has to reopen or roll the segment if its handle is poisoned. The
     account-level hard HOLD policy stays with NC-02.
6. **Journal kinds:** `incident_recorded` is gone. Incidents wait for an NC-01/NC-02 record type.

## 7. Open for Codex, and conflicts

- **C1.** NC-01 `SYMBOL_RE` allows `{2,30}`, but the transport allows `{2,20}`.
- **C2.** The incident record type is missing (deferred, see §6).
- **C3.** NC-01 has no ordinal or route field. Lineage is proven by derivation plus the journal count, and the route by
  the client id. A future NC-01 r3 could carry them explicitly.
- **C4.** The transport outcome is flattened in TestnetVenue, which needs a pure mapping. The port never builds an
  `OrderResult`.
- **Decisions from r1 still standing:**
  - the account is in every hash;
  - consumption at `decision_recorded`;
  - the `zbn1o-`/`zbn1a-` client-id format, frozen here and re-exported by NC-03;
  - no retry under a consumed key;
  - NC-02's emergency stop renamed to `zbn1e-`.

**Not in step 0:**
- adapters;
- instrument, equity and funding reads, and the clock port;
- recovery, reconcile, risk, manage and the Runner;
- multi-leg decisions, manual entries, makers, trailing entries and the websocket;
- any mainnet path.
