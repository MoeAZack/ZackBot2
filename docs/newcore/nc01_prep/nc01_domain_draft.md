# NC-01 domain model / reason codes: draft records (PRE-STAGE, Claude Code)

Base evidence: `origin/master` @ `cff3f88` and PR #34 head `6150ef4`. The runnable sketch of every record below is
`nc01_domain_sketch.py` in this folder. `nc01_sketch_smoke.py` round-trips a portfolio and shows each Audit2 negative fixture being
rejected. Run it with `python -I nc01_sketch_smoke.py`; it ends with `ALL OK`.

## Record style (recommendation)

- Use stdlib `@dataclass(frozen=True, slots=True, kw_only=True)`. This needs no dependency, the records are hashable, there is no accidental mutation, and
  `dataclasses.replace` is the only way to "change" a record. Do not use pydantic: it adds a dependency and a second validation dialect.
- **All invariants live in `__post_init__`.** Decoding goes through the constructor, so an invalid record cannot exist in memory or come
  back from disk. Errors are `InvalidRecord(path, msg)`. A newer schema raises `FutureSchema` **before** any other check, as a separate class from
  damage.
- Collections are `tuple`s (immutable). Quantities and prices are `Decimal` (JSON as decimal strings). Ids are opaque `prefix_<uuid4 hex>`:
  `acct_`, `lot_`, `ent_`, `int_`, `dec_`. Client order ids keep the legacy `new_cid` shape `z[a-z][0-9a-f]{22}`.
- Timestamps: the target is `int` UTC epoch ms (ADR D1.4); the sketch uses float/ISO for brevity.
- Codec: `encode_document(obj) -> {schema_version, kind, body}` and `decode_document(doc)`. Unknown keys and missing keys are rejected,
  bool is never a number, NaN/Inf are rejected, and type hints are cached (~126 µs to decode+validate a 1-lot portfolio on Py 3.14).

## Enums

| Enum | Values | Replaces |
|---|---|---|
| `Side` | LONG, SHORT | `SIDES` strings |
| `Environment` | backtest, sim, testnet, mainnet | `install.account.mode` paper/live + base URL |
| `EntriesMode` | active, paused, halted, flattening | `ENTRIES_PAUSED` (settings) + `state.halted` + implicit flatten |
| `Ownership` | known, unknown | implicit: `_load_failed` → `state=None` → "empty + UNTRACKED" |
| `Purpose` | entry, add, close, reduce, stop, cancel | `pending.kind` add/close; grid `op.kind` |
| `OrderType` | market, limit_gtx, stop_market | implicit in client calls |
| `ResultPhase` | unknown, known, final | `_order_result.state` + `AmbiguousOrder` |
| `Evidence` | exchange_final, exchange_refused, not_found_corroborated, position_adopted | implicit branches of `_resolve_pending` / `_settle_unconfirmed` |
| `ProtectionStatus` | needs_placement, placement_pending, owned_unverified, owned_confirmed, missing_checking, missing_restoring, owner_check | `stop_dirty`, `stop_id is None`, `prov_pending`, `stop_placed_t`, `stop_confirmed_t`, `stop_miss_why` ∈ `STOP_MISS_ORDER` |
| `EntryKind` / `EntryState` | market/maker/trailing; armed/working/cancelling/cancel_unknown/unresolved | three dict families + `RESTING_STATUS` |
| `Action` | protect, flatten, close, reduce, halt, pause, cancel_entry, reconcile, add, enter, hold, skip (priority order) | none: implicit call order inside `manage()` |
| `ReasonCode` | see the last section | `trade_audit._EXACT/_PREFIX/_WRAP`, exit `why` strings |

## Records

### Account + AccountBinding
| Field | Type | Legacy |
|---|---|---|
| `account_id` | `acct_<uuid>` | none (folder = account) |
| `label` | str | none |
| `binding.venue` | str (`binance-usdm`) | implicit |
| `binding.environment` | Environment | `install.account.mode` |
| `binding.base_url` | str | `install.account.base` |
| `binding.key_digest` | 16-hex PBKDF2 | `install.account.key` (`_key_digest`) |
| `binding.exchange_uid` | str? | none |
| `hedge_mode` | bool | `Engine.hedge` (shorts need it) |

Invariants: an id with the `acct_` prefix; a 16-hex digest; environment is an enum. The **binding never holds a secret**, which carries over the AUD-05 r2 rule.

### Portfolio (one per account; the unit NC-02 snapshots)
| Field | Type | Legacy |
|---|---|---|
| `account_id` | id | none |
| `generation` | int ≥ 0, strictly increasing per durable write | none (`schema_version` only) |
| `ownership` | Ownership | `integrity['state'].status` failed_closed / restored |
| `entries_mode` + `pause_reasons` | EntriesMode + tuple[ReasonCode] | `ENTRIES_PAUSED`, `halted`, `state_untrusted`, `install_mismatch` |
| `positions` | tuple[Position] \| None | `state.lots` |
| `entries` | tuple[EntryIntent] \| None | `unconfirmed_entries`, `resting_entries`, `pending_entries` |
| `intents` | tuple[OrderIntent] \| None | `lot.pending`, `ue.cid`, `prov_pending`, resting `cid`, grid `op` |
| `orphan_cancels` | tuple[(symbol, tag)] | `state.orphans` |
| `trading_day`, `day_start_equity`, `peak_equity` | Cairo date, Decimal | same names |

Invariants (rejecting each one is a test):
1. `ownership == UNKNOWN` ⇔ `positions`, `entries` and `intents` are all `None`. **Unknown can never be iterated as empty.**
2. `UNKNOWN` ⇒ `entries_mode != ACTIVE`.
3. `pause_reasons` is non-empty exactly when `entries_mode != ACTIVE`.
4. One `Position` per (symbol, side). Every lot, intent and entry carries this `account_id`.
5. Intent ids and client order ids are unique.
6. `lot.in_flight` names an intent in `intents` whose `owner_id` is that lot. `entry.order` does the same for an entry.
7. `entries_mode != ACTIVE` ⇒ every MAKER/TRAILING entry is `cancelling`/`cancel_unknown`. Pause, halt and flatten drain in the same transition (Audit2 NEW-ENG-11). A MARKET entry may stay `unresolved`: it is settled, protected, then flattened.
- Still open: at most one `Position` record per exchange position. This is the legacy hedge-mode assumption; ONE_WAY accounts are out of scope for NC-01.

### Position
| `symbol`, `side`, `lots: tuple[Lot]`; `qty` is **derived** (Σ lot.qty), never stored. |
Invariants: non-empty (an empty position is not stored), lot ids unique, every lot has the same symbol and side. Legacy: implicit
`groups[(sym, side)]` in `reconcile`.

### Lot
| Field | Type | Legacy field |
|---|---|---|
| `lot_id` | `lot_<uuid>` | dict key `"{sleeve}\|{sym}\|{side}\|{int(time.time())}"`. Second resolution, overwrites on a collision, parsed by `flatten` (`k.split('\|')[1]`) |
| `account_id`, `symbol`, `side` | | `symbol`, `side` |
| `source` | strategy/manual/adopted | `manual`, adoption in `_settle_unconfirmed` |
| `slot_id`, `strategy_key`, `tf` | | `sleeve` (`'MAN'` sentinel), `key_strategy`, `tf` |
| `opened_at` | ts | `opened` |
| `qty` > 0 | Decimal | `qty` (legacy allows 0, which leaves a "ghost lot" until `_finish`) |
| `avg_price`, `entry_price`, `anchor_price` | Decimal > 0 | `avg`, `entry0`, `e0` |
| `initial_qty`, `max_qty` | Decimal > 0 | `q0`, `qty_max` |
| `risk_distance`, `risk_usd` | Decimal | `R`, `risk_usd` |
| `stop` | Protection (kind STOP, owner = this lot) | `stop`, `stop_id`, `stop_dirty`, `stop_miss*`, `stop_foreign`, `stop_confirmed_t`, `stop_placed_t` |
| `target` | Protection? | `tp` (DCA/basket/manual) |
| `in_flight` | intent id? | `pending{kind,qty,px,why,post,t,cid}`: **cid optional** in legacy |
| `tp1_done`, `ladder_done`, `adds_done`, `dca_filled` | bool, tuple[int], int, int | `tp1`, `tps_done`, `adds`, `dca` |
| `realized`, `fees`, `fills: tuple[Fill]` | Decimal | `realized`, `fees`, `fills=[[iso, why, q, px]]` |
| `restored_unverified` | bool | `restored_from_bak` + `restored_mismatch` |
| `add_cooldown_until` | ts? | `add_retry_at` |
Plan-derived values (`levels`, `w`, `next_add`, `best`, `atr0`, `atr_now`, `breaker_dca`, `share`, `lev_exception`, `zero_partials`,
`ap`/`ex` audit blobs) move out of the lot into a `ManagementPlan` record (NC-07) and an NC-09 tracker. They are **not** NC-01 fields.

Invariants: `qty > 0`; prices and sizes > 0; `qty ≤ max_qty`, `initial_qty ≤ max_qty`; `slot_id` is set ⇔ source = strategy; the stop
record belongs to this lot; a placed stop covers exactly `qty` unless the stop is `needs_placement` or an order is in flight; `ladder_done` is sorted
and unique; counters ≥ 0. Transition-level invariants (NC-07, not the constructor): `tp1_done` and `ladder_done` only grow, and
`dca_filled ≤ len(plan.levels)`.

### OrderIntent (durable before the send: the WAL record)
| Field | Type | Legacy |
|---|---|---|
| `intent_id` | `int_<uuid>` | none |
| `account_id` | id | none |
| `client_order_id` | **required** bot cid | `pending.cid` (optional), `ue.cid` (optional), resting `cid` |
| `alt_client_order_id` | algo fallback cid? | `prov_pending.alt` (`ac:`) |
| `purpose`, `order_type` | enums | `pending.kind`, call site |
| `symbol`, `side` (position side), `qty` > 0 | | `qty` |
| `price` / `stop_price` | Decimal? | `px` (estimate), stop price |
| `reduce_only` | bool | implicit |
| `reason` | ReasonCode | `pending.why` |
| `owner_id` | `lot_`/`ent_` | the dict that holds it |
| `created_at` | ts | `pending.t` |
Invariants: a valid cid (**a qty-only pending cannot be represented**); `reduce_only` ⇔ close/reduce/stop; limit ⇒ price, otherwise
none; stop ⇔ `stop_market` ⇔ `stop_price`; ENTRY owned by an `ent_`, ADD/CLOSE/REDUCE owned by a `lot_`; an `exit.*` reason only on close/reduce.
Legacy `post={...}` (the bookkeeping applied on fill) is **dropped**. NC-07 recomputes the follow-up from the final result plus the plan
(legacy `post.finish` becomes "the result closed the whole lot").

### OrderResult (AUD-03 known / unknown / final)
| Field | Type | Legacy |
|---|---|---|
| `intent_id`, `client_order_id` | | `cid` |
| `phase` | unknown / known / final | `AmbiguousOrder` / `state='pending'` / `state='final'` |
| `requested_qty` | Decimal > 0 | `requested` |
| `executed_qty`, `avg_price` | only when final | `_exec_of`, `_fill_px` |
| `exchange_status` | str? | `status` |
| `evidence` | Evidence, only when final | implicit |
| `observed_at` | ts | none |
Invariants: non-final ⇒ no executed qty, price or evidence (a `PARTIALLY_FILLED` executedQty is never booked). Final ⇒ evidence and
`0 ≤ executed ≤ requested`. `executed > 0` ⇒ `avg_price > 0` and evidence ∈ {exchange_final, position_adopted}. `executed == 0` ⇒
evidence ∈ {exchange_final, exchange_refused, **not_found_corroborated**}. A bare not-found is not an `Evidence` value, so the
legacy "notfound older than 20 s ⇒ never executed" (`_resolve_pending`, the Audit2 NEW-ENG-04 repro) cannot be stored as final.
`not_found_corroborated` is produced only by NC-03 when not-found is backed by an unchanged position on ≥ 2 reads past the visibility
window. `position_adopted` (legacy `UNCONF_ADOPT_S` adoption at mark) is flagged in observability, because its price is an estimate.
`unreadable`/`nosource` (legacy) are venue query outcomes in NC-03, not result phases.

### Protection (AUD-04 stop/target ownership)
| Field | Type | Legacy |
|---|---|---|
| `owner_id` | `lot_`/`ent_` | the lot / the unconfirmed entry (`prov`) |
| `kind` | stop / target | stop only in legacy (targets are market closes) |
| `status` | ProtectionStatus | see the enum row |
| `price`, `qty` | Decimal > 0 | `stop`; `prov_stop`/`prov_qty` |
| `tag`, `alt_tag` | owned order tags | `stop_id`; `prov`, `prov_pending.tag/alt` |
| `foreign_tag` | tag? | `stop_foreign` |
| `placed_at`, `confirmed_at`, `miss_count` | | `stop_placed_t`, `stop_confirmed_t`, `stop_miss` (+`stop_miss_t`) |
Invariants: owned/missing states need `tag`. `placement_pending` is owned by its `c:` client id (plus the optional `ac:` alt). `owner_check` names the own
or the foreign order. `foreign_tag` is only allowed in `owner_check` and is **never a bot cid** (legacy `BOT_STOP_CID_RE` rule). `confirmed_at` is set ⇔
`owned_confirmed`. `miss_count > 0` ⇒ a missing or owner_check state. A provisional stop's `qty ≤ entry.seen_qty` (AUD-03 r1: never larger than what it
protects). `blocks_entries` = every status except owned_unverified/owned_confirmed, which replaces `STOP_MISSING_BLOCKS`, the `stop_dirty` gate and
`stop_missing_block`. Freshness (`STOP_FRESH_S`) is a read-model function, not a field.

### EntryIntent (**not in the plan's list; proposed**: the three legacy entry families)
| Field | Legacy |
|---|---|
| `entry_id`, `account_id`, `kind`, `state` | dict keys `UE\|…`, `ME\|…`, trailing key; `RESTING_STATUS` |
| `symbol`, `side`, `slot_id` | same |
| `planned_qty`, `planned_price`, `stop_distance` | `ue.qty`, `plan.px`, `plan.stop_dist` |
| `created_at`, `expires_at` | `t`/`t0`, trailing `until` |
| `order` (intent id) | `cid` |
| `filled_qty`, `seen_qty` | resting `filled`/`cost`; `ue.seen_qty` |
| `provisional_stop: Protection?` | `prov`, `prov_qty`, `prov_stop`, `prov_pending` |
Invariants: trailing ⇒ armed, no order, has an expiry; maker/market ⇒ not armed and owned by an order intent; `filled ≤ planned`; a provisional stop
only on a market entry and `≤ seen_qty`. The legacy `plan` dict (whole sizing context, risk, signal) becomes a reference to the
Decision that created the entry (`dec_` id).

### Decision
| Field | Notes |
|---|---|
| `decision_id`, `account_id`, `at` | |
| `action`, `reason: ReasonCode`, `detail ≤ 160` | replaces `last_skip` text, `miss()` records and the exit `why` |
| `symbol`, `side`, `subject_id` | |
| `intents: tuple[OrderIntent]` | what the shell sends (after WAL) |
| `policy_version` | replaces `trade_audit.POLICY_VERSION`/`ap.v` |
Invariants: skip/hold ⇒ no intents. Skip ⇒ the reason has a gate stage. Close/reduce ⇒ an `exit.*` reason and only close/reduce intents. Enter ⇒ at most
one entry intent. Every intent belongs to the same account. `priority` comes from `Action`.

## ReasonCode seed (value = `stage.code`, append-only)

Gate codes are taken 1:1 from `trade_audit._EXACT/_PREFIX/_WRAP`, so the stage/code values match `reason_info` today:
`connectivity.{not_connected,exchange_outage}` · `input.bad_direction` · `side_mask.hedge_off` ·
`filter.{halt,paused,coin_off,slot_off,slot_removed,hours,volatility,warning,ai_veto}` ·
`capacity.{in_trade,entry_working,leverage_cap,max_positions}` · `regime.{regime,regime_unknown}` ·
`risk_gateway.{stop_unconfirmed,untracked_position,pump_guard,not_tradable,state_untrusted,account_unconfirmed}` + risk rules
`risk_gateway.{coin_cap,open_risk_cap,correlated_cap,btc_breaker,funding_filter}` (from `RISK_RULE_DEFAULTS`; legacy slugs
them from the "risk rule <name>:" text) · `config.{dca_no_stop,dca_range}` ·
`execution.{entry_unconfirmed,entry_unfilled,entry_unconfirmed_wait,stop_failed,maker_unfilled,maker_fallback_blocked,size_min,
write_ahead_failed,order_failed,leverage}` · `trailing.expired` (trailing cancels keep the inner gate code, as `_WRAP` does).
`filter.ai_veto` and `filter.warning` are seeded as **deprecated**: there is no AI filter in NEWCORE, and a warning is an observe event, not a gate.

Exit codes (legacy `why` → golden projection): `exit.stop`→STOP_HIT · `exit.stop_crossed`→STOP_HIT · `exit.time_exit`→TIME_EXIT ·
`exit.exit_signal`→SIGNAL_EXIT · `exit.take_profit`→TP_FULL · `exit.take_profit_1`→TP_PARTIAL · `exit.basket_tp_part`→TP_PARTIAL ·
`exit.take_profit_ladder`→TP_LADDER · `exit.basket_tp`→TP_BASKET · `exit.liquidated`→LIQUIDATED · `exit.flatten`→FLATTEN ·
`exit.resync`→(none) · `exit.stop_failed`→(none) · `exit.manual`→(none).

New codes for recovery/protection/order outcomes: `protect.{place,checking,restoring,owner_check}` (from `STOP_MISS_ORDER`) ·
`recovery.{schema_future,schema_invalid,state_missing_initialized,restored_from_backup,account_mismatch}` (AUD-05 incident keys) ·
`order.{not_found_uncorroborated,late_fill_not_active}` (Audit2) · `operator.{pause,flatten}`.
Add-block texts (`_add_block`) map onto the same gate codes (halt, paused, outage, stop_unconfirmed, …). The legacy
`'manual trade (no adds)'` and `'strategy slot removed or replaced'` become `capacity.manual_no_adds` and `filter.slot_removed`.
