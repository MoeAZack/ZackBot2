# NC-01 test shapes (PRE-STAGE, Claude Code)

The target layout is `tests/newcore/domain/`. Everything is stdlib plus pytest, deterministic, and fast-path (no network, no clock, no disk except
`tmp_path` in codec tests). Property tests use a **seeded stdlib generator** (`random.Random(seed)`, seeds listed in the test,
N=500 per property, failing seed printed). The alternative, `hypothesis` as a dev-only dependency, is a Codex call (it is not in `requirements.txt`).

## 1. Schema / codec tests (`test_codec.py`)

| Test | Shape |
|---|---|
| round trip | For every record kind, `decode(encode(x)) == x`, and `canonical(encode(decode(encode(x))))` is byte-identical (sorted keys, no whitespace, decimal strings). |
| canonical stability | A pinned golden JSON per record kind (`fixtures/domain/*.json`). Re-encoding it must equal the file. This catches silent field renames or reorders. |
| strict numbers | `qty` ∈ {`"NaN"`, `"Infinity"`, `"1e999"`, `1.5` (JSON float), `true`, `null`, `" 1"`, `"1_0"`} ⇒ `InvalidRecord` naming the exact path. This mirrors golden `schema.finite` and legacy `_num` (bool is never a number). |
| unknown / missing keys | Adding a key or deleting any required key at any depth ⇒ `InvalidRecord(path)`. Generated: for every field path of a valid document, mutate it and expect rejection at that path. |
| future schema | `schema_version` ∈ {2, 99} with **any** body (valid, garbage, missing) ⇒ `FutureSchema`. A test spy asserts the body was never visited (FutureSchema is checked first). |
| current-schema damage | Version ∈ {0, -1, "1", true, 1.0, missing} ⇒ `InvalidRecord`, never `FutureSchema`. |
| enum closure | Every enum value round-trips. An unknown string ⇒ `InvalidRecord`. |
| id shape | Every id field rejects time-derived keys (legacy `"S1|SOLUSDT|LONG|1728380000"`), the wrong prefix and uppercase hex. |
| decode budget | Decoding plus validating a 40-lot portfolio stays under a pinned budget (sketch: ~126 µs for 1 lot). This is a perf guard, not a benchmark. |

## 2. Invalid-state rejection (`test_invariants.py`)

There is one parametrized case per constructor invariant. A valid factory builds the record, `dataclasses.replace` breaks exactly one field,
and the test expects `InvalidRecord` whose `.path` ends with that field. The table is the checklist; each row comes from the domain draft:

- Portfolio 1–7 (unknown ⇔ None; unknown ⇒ not active; pause_reasons ⇔ not active; one position per symbol/side; account match;
  unique intent ids and cids; `in_flight`/`order` name an owned durable intent; non-active ⇒ makers/trailing draining).
- Position (non-empty, unique lot ids, symbol/side match). Lot (qty>0, prices>0, qty ≤ max_qty, slot ⇔ strategy, own stop,
  stop qty == lot qty unless needs_placement/in-flight, sorted ladder). OrderIntent (cid, reduce_only ⇔ purpose, price/stop_price
  per type, owner prefix per purpose, exit reason only on close). OrderResult (non-final has no executed/price/evidence; final evidence
  rules). Protection (tag per status, foreign only in owner_check and never a bot cid, confirmed_at ⇔ confirmed, misses ⇒ missing
  state, provisional ≤ seen). EntryIntent (trailing armed/no order/expiry; others owned by an order; filled ≤ planned). Decision (skip/hold
  send nothing; skip ⇒ gate stage; close ⇒ exit stage; enter ≤ 1 entry intent; same account).

## 3. Property tests (`test_properties.py`)

Generators build **valid** records from seeds: random symbol, side, step-aligned Decimal qty/price, 0–5 lots per position, 0–3
entries/intents with consistent ownership, and random protection statuses with their required fields.

| Property | Statement |
|---|---|
| P1 closure | Every generated record constructs, round-trips and re-encodes canonically. |
| P2 single-field mutation | For a random field path and a random wrong-type or out-of-range value, either the record still satisfies every invariant (re-checked by an independent `check()` oracle written as plain predicates) **or** construction raises `InvalidRecord`. Never a silent accept that the oracle rejects. |
| P3 derived qty | `Position.qty == Σ lot.qty` exactly (Decimal), for any lot permutation. |
| P4 ownership totality | Every exchange-side reference in a portfolio (lot stop tags, provisional tags, intent cids, orphan tags) is unique, and the set equals `owned_tags(portfolio)`. This is the NC-01 form of legacy `_owned_stop_tags` + `ownership_present`. |
| P5 unknown is not empty | For any UNKNOWN portfolio, `portfolio.lots` iteration is impossible via the fields (`positions is None`), and `owned_tags` raises `OwnershipUnknown` instead of returning ∅. |
| P6 priority total order | `sorted(decisions, key=priority)` puts every PROTECT/CLOSE before any ADD/ENTER, for random decision lists. |
| P7 reason stage ↔ action | For random (action, reason) pairs, construction succeeds ⇔ the allowed-stage table says so. |
| P8 no float leak | No encoded document contains a JSON float for any `Decimal` field (walk the encoded tree). |

## 4. Reason-code tests (`test_reason_codes.py`)

- Every value matches `^[a-z_]+\.[a-z0-9_]+$` and is unique. `stage` ∈ the declared stage set.
- **Append-only:** a pinned file `fixtures/domain/reason_codes.txt` lists every value ever released. The current enum ⊇ that file, and a
  removed or renamed value fails. This is the same idea as the golden append-only ledger.
- **Legacy seed coverage (temporary, deleted at Checkpoint C):** every `trade_audit._EXACT` value, every `_PREFIX` and every `_WRAP` code,
  and every `RISK_RULE_DEFAULTS` key map to an existing `ReasonCode` whose value equals `f'{stage}.{code}'`.
- Every `exit.*` code is a key of `GOLDEN_EXIT`. Every non-None projection ∈ `goldenlib.schema.EXIT_CODES`. Every golden EXIT_CODE has
  at least one preimage (today `FLATTEN` has one only because of `exit.flatten`; legacy_engine reports it as `UNMAPPED:flatten`).

## 5. Boundary test (`test_import_graph.py`)

An `ast` walk over `zackcore/domain/**` asserts imports ⊆ stdlib and that `domain` imports no other `zackcore` package. The same test
checks the module edge table from ADR §2 for the packages that exist.

## 6. Audit2 negative fixtures → what makes them unrepresentable or detectable

| Audit2 category (Codex repro on `cff3f88`) | Legacy mechanism | NC-01 record / invariant | NC-01 test | Behaviour proof (later ticket) |
|---|---|---|---|---|
| **Future schema** (`schema_version=2` in both copies → both quarantined, zero lots, position left) | `_version` raises `SchemaError` (a `CorruptFile`), handled like damage by `_load_safe` | `decode_document` raises **`FutureSchema`**, a distinct class checked before anything else | §1 "future schema" (body never visited) | NC-02: abort, both files byte-identical, no quarantine (REC-01) |
| **Current-schema damage** | `SchemaError` → quarantine → `.bak` or failed-closed empty | `InvalidRecord(path)` from any constructor; the result can only be a `Portfolio(ownership=UNKNOWN, positions=None…)` | §1 strict numbers / unknown keys + §2 Portfolio 1–2 | NC-02: evidence preserved, entries paused, reconciliation required |
| **Empty managed account** (missing/corrupt primary + backup → managed empty account) | `_load_failed` → `state=None` → default `dict(lots={})` → exchange size reported "UNTRACKED" | `Ownership.UNKNOWN` ⇔ collections `None`; UNKNOWN ⇒ not ACTIVE; only `Portfolio.fresh(account)` (first run with no install evidence, NC-02 decides) yields KNOWN+empty | §2 Portfolio 1–2, P5 | NC-02 crash matrix: no path from failed load to KNOWN-empty |
| **Qty-only pending** (resolved in memory, disk keeps the old qty + pending) | `lot.pending` with optional `cid`; position-only resolution; no save on that path | `OrderIntent.client_order_id` **required**; `lot.in_flight` must name a durable intent; a result is an event (ADR D1.2) | §2 OrderIntent cid, Portfolio 6 | NC-02/03: `ResultObserved` durable before apply; restart replays to the same qty |
| **False not-found** (notfound >20 s drops the pending add; a new add takes the position 1.5→2.0) | `_resolve_pending`: `fr['state']=='notfound' and age>20` → `del pending` | `OrderResult` FINAL with executed 0 needs evidence ∈ {exchange_final, exchange_refused, **not_found_corroborated**}; a bare not-found has no `Evidence` value; non-final carries no qty | §2 OrderResult, P2 | NC-03: corroboration needs an unchanged position on ≥2 reads past the window; the ADD gate refuses while `in_flight` is set |
| **Resting-maker late fill** (flatten leaves a maker; a later fill opens a protected lot while paused) | `flatten()` never touches `resting_entries`; `_maker_finalize` → `_create_lot` with no mode check | Portfolio invariant 7: non-ACTIVE ⇒ maker/trailing entries are `cancelling`/`cancel_unknown`, so PAUSE/FLATTEN must emit `CANCEL_ENTRY` intents in the same transition; `ReasonCode.order.late_fill_not_active` for the late-fill Decision | §2 Portfolio 7 | NC-05/07: a late fill under non-ACTIVE ⇒ Decision PROTECT then CLOSE (`exit.flatten`), never a managed lot |
| (also) **lock / network under state lock** (NEW-ENG-02) | `manage()` holds `self.lock` across HTTP calls | not representable in data; frozen records make lock-free snapshots possible | none | NC-05 deadlock/latency tests |

## 7. Out of scope for NC-01 (stated so review does not expect it)

Transitions (NC-07), persistence/crash matrix (NC-02), venue parsing (NC-03), scheduler/locks (NC-05), sizing (NC-06), golden
`newcore_*` adapters (NC-07/08). NC-01 ships records, the codec, `ReasonCode`, `GOLDEN_EXIT`, `owned_tags()`, and the tests above.
