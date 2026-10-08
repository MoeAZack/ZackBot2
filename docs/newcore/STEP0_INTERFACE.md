# NEWCORE step 0: the shared interface frozen before parallel slice work

**State:** PROPOSED by Claude Code (Build) for Codex ruling. 08 Oct 2026, Africa/Cairo. Base: `master` @ `422c60e`.
**Code:** `newcore/ports/` (Protocols + tiny value types, no adapters), tests in `tests/newcore_ports/`.
**Ruling implemented:** canonical decision key, restart-stable caller-supplied intent id + consumed-signal rule, one
JournalPort event grammar, fixed VenuePort and a separate BarSource.
**Inputs:** NC-01 contract r2 (`pr36`), local `nc-01-domain` @ `d703e30`, `nc-strategy-ema-mom` @ `d21d113`,
`nc-venue-testnet` @ `0a0c7f3`, slice plan and `nc02_design.md` (`prep/nc02-negative-fixtures`).

`newcore.ports` imports only the stdlib. It mirrors NC-01 values (ms range, id shape, symbol, side and purpose
spellings) and is **bound to `newcore.domain` when NC-01 lands**; until then it does not import it.

## 1. DecisionKey

| Field | Type / rule | Example |
|---|---|---|
| `strategy` | 1..32 printable; the strategy **instance**, timeframe included | `trend_ema_mom@4h` |
| `strategy_version` | 1..32 printable; the rule version | `v1` |
| `symbol` | `[A-Z0-9]{2,30}` | `BTCUSDT` |
| `side` | `LONG` / `SHORT` (position side) | `LONG` |
| `candle_close_ms` | int UTC ms; signal candle `open_ms + tf_ms` (= Binance `closeTime + 1`) | `1759924800000` |
| `purpose` | `entry` / `add` / `reduce` / `close` / `protect` | `entry` |

Canonical bytes are **byte-identical to NC-01 `codec.canonical_bytes(DecisionKey)`** (checked against `nc-01-domain`
for 90 combinations): UTF-8 JSON, sorted keys, no whitespace, ASCII escapes, the NC-01 envelope.

```
{"body":{"candle_close_ms":1759924800000,"purpose":"entry","side":"LONG","strategy":"trend_ema_mom@4h",
"strategy_version":"v1","symbol":"BTCUSDT"},"format":"zackbot.newcore","record_type":"decision_key","schema_version":1}
```

`DecisionKey.from_canonical` is strict: exact keys, no duplicates, no float/bool/text timestamp, canonical spelling only.
From the strategy: rule `trend_ema_mom.v1` gives `strategy='trend_ema_mom@<tf>'` and `strategy_version='v1'`;
`signal_time_ms` is `candle_close_ms`; `SignalAction` ENTER/CLOSE is purpose `entry`/`close`.

## 2. Intent id rule and the consumed signal

`H(tag, parts...) = sha256(b"zackbot.newcore.<tag>.v1" + b"\0" + part ...)`; no part can contain NUL. Ids are the
`<prefix>_` plus the first 32 hex characters.

| Id | Derivation |
|---|---|
| `decision_id` | `dec_ H(decision_id, account_id, key_bytes)`: one decision per (account, key) |
| `intent_id` | `int_ H(intent_id, account_id, key_bytes, leg)`, leg 0..15 (VS-01 uses leg 0) |
| child `intent_id` | `int_ H(child_intent_id, account_id, parent_intent_id, purpose, ordinal)`: the entry's stop, its replacements, drains; `ordinal` = intents of that purpose the journal already holds for the parent |
| `client_id` | `"zbn1" + ("o" classic \| "a" algo) + "-" + base32(H(client_id, intent_id, route))[:26]` |

- **Client ids** are 32 characters (Binance's limit is 36). The charset `[a-z2-7-]` is inside `[.A-Z:/a-z0-9_-]`, and
  an id can never match the legacy `z[ab][0-9a-f]{22}`. `is_newcore_client_id` is a shape test for owned-vs-foreign
  triage; it is never parsed.
- **Restart-stable:** every id is a pure function of journaled data, with no clock and no randomness. Pinned vectors in
  `test_ports_keys.py` turn any change red; a change means new `.v2` tags, never an edit. A 102,400-derivation sample
  shows no collision across decision ids, intent ids and client ids.
- **Consumed signal:** a DecisionKey is consumed when its `decision_recorded` is durable (ENTER, SKIP or WAIT alike).
  - `claim_signal(grammar, key)` answers a re-delivered signal (restart, duplicate bar, replay) with `fresh=False`, the
    same decision id and the intents already recorded. It never gives new ones.
  - A crash between the decision and its intent leaves the key consumed with no intents. Only the derived id can still
    be recorded, and only if the runner judges the entry still in window.
  - A REJECTED or NOT_SENT keyed intent is not retried under the same key: the signal is spent (fail closed).
  - Grammar G4/G5 enforce all of this.

## 3. JournalPort and its event grammar

- `append(event) -> Admission`: `APPLY` only after fsync; `ALREADY_APPLIED` for an identical re-append. It raises
  `JournalConflict` (grammar) or `JournalUnavailable` (store failure: nothing may be sent).
- `last_sequence()`, `read(after_sequence)`, `find_decision(decision_id)`.

Events are NC-01 DomainEvents. The grammar runs over `EventHeader`: kind, ids, sequence, `at_ms`, and
`digest = contract_sha256(event)`.

| Kind | NC-01 / NC-02 record | Header fields |
|---|---|---|
| `decision_recorded` | DecisionRecorded | `decision_id`, optional `decision_key` |
| `intent_recorded` | IntentRecorded (DURABLE, before send) | `decision_id`, `intent_id`, `purpose`, `client_ids` |
| `sent` | IntentStateChanged durable→submitted | `intent_id` |
| `state_changed` | IntentStateChanged → working / unknown / cancelling | `intent_id`, `to_state` |
| `result_recorded` | ResultObserved (durable before apply) | `intent_id`, `outcome` final/known/unknown/not_found, `evidence` (final only) |
| `intent_closed` | IntentStateChanged → filled / cancelled / rejected / not_sent | `intent_id`, `to_state` |
| `mode_changed` | ModeChanged (HOLD in/out, pause, halt, resume) | none |
| `binding_changed` | BindingChanged | none |
| `incident_recorded` | NC-02 IncidentRecorded | none |

**Protection has no separate events.** `protection_*` is the `purpose=protect` intent's own `intent_recorded`, `sent`,
`state_changed`, `result_recorded` and `intent_closed`. This keeps NC-01 invariant 5 (one order lifecycle) and leaves
Protection status derived.

**Rules.** A refused event changes nothing.
- **G1:** one journal per account and aggregate.
- **G2:** `sequence` = last + 1. A gap, reorder or reused sequence is a hard failure; a timestamp never orders events.
- **G3:** a known `event_id` with the same sequence and digest is a no-op; anything else is a conflict (NC-01
  invariant 12, `ledger.admit`).
- **G4:** the decision id is new; a keyed decision's id is `derive_decision_id`.
- **G5:** the intent id is new and never re-recorded; its decision was recorded earlier; a keyed decision's intents use
  derived ids and the key's purpose; client ids are exactly `client_id_for(intent, classic[, algo])` (algo for protect
  only) and are never reused.
- **G6:** `sent` once, only from recorded.
- **G7:** `working`/`unknown` only after `sent`; `cancelling` also before it. After a final result only the close.
- **G8:** no result before the intent is recorded or after the final one; a never-sent intent takes only
  `final`+`not_sent`; `not_found` books nothing.
- **G9:** close only after a final result; `not_sent` close ⇔ `not_sent` evidence; nothing follows a close.

The grammar checks order and idempotence only. Record content and the fine lifecycle table stay with NC-01
`check_event_chain`.

## 4. VenuePort

Synchronous; one request per call; never retries or switches route by itself.

| Call | Returns |
|---|---|
| `submit_market(MarketOrder(ref, position_side, qty, reduce))` | `OrderOutcome` |
| `submit_stop(StopOrder(ref, position_side, qty, stop_price))` (reduce-only, mark price) | `OrderOutcome` |
| `cancel(OrderRef(symbol, client_id, route))` / `query(OrderRef)` | `OrderOutcome` |
| `positions(symbol=None)` / `open_orders(symbol=None)` (classic and algo) | `ReadOutcome` of `VenuePosition` / `VenueOrder` |
| `fills(symbol, exchange_order_id)` | `ReadOutcome` of `VenueFill` |

- **Outcome kinds** equal the transport's `OrderOutcomeKind` values: `known`, `final`, `acknowledged`, `rejected`,
  `unknown`, `not_found`. Only `final` carries `executed_qty`.
- **NC-01 OrderResult mapping:** final → FINAL `exchange_final`; a submit `rejected` → FINAL `exchange_refused` with
  nothing executed; unknown → UNKNOWN; not_found → UNKNOWN with `lookup=not_found`.
- **Reads** are ok / rejected / unknown. An unknown read has no value: unknown is never empty.
- **Adapters:** `TestnetVenue` maps `MarketOrder` to `place_market`, `StopOrder` to `place_stop_market(route)`, and
  `OrderRef` to query/cancel and their `_algo` variants, flattening `record`/`error`. `FakeVenue` implements the same
  port.

## 5. BarSource

`closed_bars(symbol, tf_ms, *, as_of_ms, limit) -> ReadOutcome[tuple[Bar]]`. A `Bar` has Decimal OHLCV, integer-ms
`open_ms`, and `close_ms = open_ms + tf_ms`. Every answer passes `check_closed_bars`: aligned, contiguous, oldest
first, every `close_ms <= as_of_ms`. The forming candle is never returned. CSV and kline sources are separate
implementations, and the strategy cannot tell them apart.

## 6. Decisions Codex must approve, and conflicts found

1. **Timeframe in `strategy`.** The five-field key has no timeframe, so 4h and 15m runs of one rule would collide on
   shared candle closes. Recommend instance names like `trend_ema_mom@4h`; the alternative is a sixth NC-01 field.
2. **`candle_close_ms` = open + tf** (the strategy's `close_ms`). Binance `closeTime` is one less; adapters add 1.
3. **Account in the hash:** the same signal on two accounts gives two intents.
4. **Consumption point:** `decision_recorded`, including SKIP/WAIT, not the first intent.
5. **Client-id format `zbn1o-`/`zbn1a-`.** It replaces the slice plan's `zb-<...[:24]>` with `attempt`: one id per
   intent and route, and a re-send reuses it. NC-01 §7 gives the format to NC-03; this freezes it in ports and NC-03
   re-exports it.
6. **Child-intent ordinal** for the stop and its replacements. NC-02's `zb-es-` emergency stop (A23) stays separate;
   recommend renaming it to `zbn1e-`.
7. **No retry under a consumed key:** a rejected entry is not re-sent for the same candle.

**Conflicts:**
- **C1.** NC-01 `SYMBOL_RE` allows `{2,30}`; the transport allows `{2,20}`. The ports follow NC-01; one must align.
- **C2.** `incident_recorded` has no NC-01 record type, and NC-01's EVENT_TYPES and codec are closed. NC-02 or NC-01 r3
  must add it.
- **C3.** The ruling's "close" and "hold" are bound to `intent_closed` and `mode_changed`; `protection_*` is bound to
  protect-purpose intent events.
- **C4.** The transport `OrderOutcome` holds raw `record` objects; the port flattens them through a pure mapping in
  TestnetVenue. The port never builds an NC-01 `OrderResult` (it needs a caller-injected `result_id`).
- **C5.** In hedge mode, `reduce` and stops must not send `reduceOnly` (-1106). The transport already enforces this.

**Agreed:** NC-01 `DecisionKey`, `Side`, `Purpose`, `Admission` and the ms range; the strategy's `Side` and
`SignalAction`.

## 7. Not in step 0

- Adapters: FakeVenue, TestnetVenue, CSV/kline BarSource, MemoryJournal and the NC-02a file journal.
- `header_of(DomainEvent)`, written when NC-01 merges.
- Instrument rules, equity, funding/income reads and the clock port.
- Snapshots, recovery, reconcile, risk, manage and the Runner.
- Maker/limit orders, trailing entry, websocket, request-weight budget, multi-account, one-way-mode policy.
- Any mainnet path.
