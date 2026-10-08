# NC-01 domain model and reason-code contract

**State:** READY for implementation  
**Published by:** Codex integration lane  
**Exact base:** protected `master` at `1a24e70bebe01e64d3f4f4325e1bce903b663282`  
**Time:** 08 Oct 2026, Africa/Cairo

NC-01 creates NEWCORE's pure domain language. It does not trade, persist, schedule, reconcile, size or select a strategy.
Its job is to make invalid ownership and ambiguous exchange outcomes impossible to represent accidentally, so NC-02+
can implement persistence and execution without inheriting the legacy engine's dictionaries and parallel state families.

## 1. Scope boundary

Claude Code owns one new package under `newcore/domain/` plus focused tests. The package:

- has no imports from `engine.py`, `app.py`, `backtest.py`, exchange clients or legacy strategy modules;
- performs no file, network, clock, environment, logging or global-state access;
- uses frozen typed records and pure validation/transition helpers;
- uses explicit versioned codecs; no pickle and no permissive object construction;
- changes no legacy product file or behavior.

NC-01 defines durability-required intent/result states, but NC-02 owns the append-only store and crash protocol. NC-03
owns exchange translation. NC-06 owns sizing/risk calculations. NC-07 owns management transitions. NC-08 owns replay.

## 2. Required records and value objects

Names may change only if the ownership and invariants remain one-to-one and Codex accepts the change.

| Type | Required meaning |
|---|---|
| `AccountId` | Stable generated identity, independent of API key or venue account name. |
| `AccountBinding` | Venue, environment, settlement/base asset and non-secret key/account digest. Rotation creates an unconfirmed binding; it never silently changes `AccountId`. |
| `InstrumentId` / `InstrumentRules` | Venue symbol plus price tick, quantity step, minimums and supported capabilities used for validation, not fetched here. |
| `Portfolio` | Account-keyed ownership aggregate with explicit trust state and immutable collections. |
| `Position` | Exchange-facing symbol/side aggregate. Long and short are distinct; no sign-based side inference. |
| `Lot` | Strategy/timeframe ownership slice with stable collision-resistant ID and explicit opened time. IDs are never parsed for business data. |
| `OrderIntent` | The sole order-ownership family. Typed purpose: `ENTRY`, `ADD`, `REDUCE`, `CLOSE`, `PROTECT`; typed lifecycle covers planned, durable, submitted, unknown, cancelling and terminal states. Resting, trailing and unconfirmed entries are not separate dictionaries. |
| `OrderResult` | Evidence-bearing known/unknown/final outcome. Bare not-found has no executed value and cannot mean never-filled. |
| `Protection` | Explicit protective-order coverage and lifecycle. Any summary status is derived from these records and exposure, never a second source of truth. |
| `Decision` | Stable reason code, authority, input/evidence references and integer UTC-ms decision time. Display text is not policy. |

All identifiers are opaque values. Orphan-cancel work is represented by the same `OrderIntent` lifecycle, not an
untyped side queue.

## 3. Non-negotiable invariants

1. **Unknown is never empty.** An untrusted portfolio/position collection is explicit `UNKNOWN` or `HOLD`, not `[]`.
   `KNOWN_EMPTY` requires a confirmed account binding, a fresh flat exchange snapshot and the later NC-02 reconciliation
   gate.
2. **Exact numbers.** Owned price, quantity, fee, funding, PnL and risk values use finite `Decimal`; floats and booleans
   are rejected. Adapters quantize against explicit tick/step rules. Zero/negative quantities are rejected where an
   exposure or order requires positive quantity.
3. **Canonical time.** Stored timestamps are integer UTC milliseconds. Cairo conversion is presentation/audit context.
4. **Stable identity.** Account, portfolio, position, lot, intent and result IDs cannot be derived from display text or
   second-resolution timestamps. Duplicate IDs are invalid.
5. **One order lifecycle.** Entry/add/close/reduce/protect ownership cannot bypass `OrderIntent`. Each transition has a
   finite allowed predecessor set; terminal states cannot return to active.
6. **Durability phases are representable.** An intent can be marked durable before send; an exchange result can be marked
   durable before applying it to ownership. NC-01 tests the state machine even though NC-02 performs the writes.
7. **Not-found is ambiguous.** It cannot carry executed quantity or close ownership. Resolution later requires final
   exchange evidence or explicit adoption corroborated by positions/orders.
8. **Protection never exceeds exposure.** Reduce-only coverage is side-correct and quantity-bounded. Replacement states
   retain old confirmed protection until new protection is confirmed.
9. **HOLD is explicit authority.** Manual entry does not bypass pause/HOLD. Protect, close, reduce and idempotent draining
   of risk-adding intents are representable emergency permissions; entry, add, reprice and market fallback are not.
10. **No legacy import.** NEWCORE begins flat on testnet. NC-01 contains no legacy state migration or compatibility shim.

## 4. Reason-code registry

- One versioned machine registry is authoritative; codes are lowercase dotted strings grouped by stable namespace.
- Codes are append-only within a schema version. Deprecated codes remain reserved and are never reused.
- Free text and translated labels are presentation only and cannot drive a transition.
- The registry distinguishes at minimum exchange stop fill from bot-triggered stop-cross close, and includes distinct
  flatten, resync, stop-failed and partial basket-target meanings.
- Every golden exit/decision code maps explicitly. Temporary legacy `UNMAPPED:*` values cannot enter NEWCORE records.
- A test scans NEWCORE constructors/transitions so a literal reason cannot bypass the registry.

The initial namespace catalog must cover ownership, binding, lifecycle, exchange evidence, protection, risk/capacity,
strategy decision, recovery, reconciliation and operator authority. New codes require a test and a short semantic entry.

## 5. Strict codec

The v1 codec must:

- round-trip every required type deterministically with canonical key ordering and Decimal strings;
- carry explicit `schema_version` and record type;
- reject unknown/missing/duplicate fields, duplicate IDs, invalid enum/reason values and cross-record reference failures;
- reject floats, NaN/infinity, booleans in numeric fields, huge/non-finite values and invalid integer-ms timestamps;
- return a typed validation failure; never coerce an invalid document to empty ownership;
- leave forward-version and persistence/recovery policy to NC-02, while exposing a distinct `unsupported_version` result.

## 6. Acceptance evidence

The implementation PR must provide:

1. table-driven unit tests for every invariant and allowed/forbidden lifecycle transition;
2. pinned Hypothesis properties plus recorded seeds for codec round-trip, invalid-state rejection and Decimal/tick/step
   boundaries;
3. tests mapping every current golden reason and the accepted expanded vocabulary;
4. tests showing the Audit2 empty-account, future/damaged schema, false-not-found, pending, resting-maker and pause/flatten
   families are rejected or represented as UNKNOWN/HOLD rather than silently resolved;
5. an AST import-boundary test and a runtime import smoke test proving the domain package is pure and legacy-independent;
6. deterministic serialization snapshots and an explicit performance baseline for representative portfolio codecs. The
   baseline is recorded before setting a regression budget; no arbitrary audit number is imported;
7. mutation evidence for numeric type checks, unknown-vs-empty, not-found ambiguity, terminal-state monotonicity,
   protection bounds, manual pause bypass and reason-code membership;
8. `git diff --check`, focused Windows tests, independent Cowork adversarial review, exact-head fast/CodeQL, then one full
   gate after review is clean.

AUD-08's short-side/vocabulary/fault expansion may be prepared in parallel. NC-01 may be implemented against this
contract immediately, but it cannot receive final acceptance until the expanded golden contract it consumes is accepted
or explicitly version-pinned as an exact reviewed artifact.

## 7. Explicitly out of scope

- exchange requests, retries, cancellation or client-ID formatting;
- files, databases, encryption, migrations, recovery or forensic envelopes;
- strategy indicators, entries/exits, DCA, runner, sizing, leverage or allocation;
- scheduler/locks, UI/API/Telegram, backtest calculations or legacy cleanup;
- mainnet activation, credentials or importing installed legacy positions.

Any need for one of these is a handoff to its owning ticket, not permission to broaden NC-01.

## 8. Handoff and lanes

- **Build — Claude Code:** implement `newcore/domain/` and focused tests from this exact base after rebasing on any earlier
  protected merge; post the exact SHA and deviations from this contract.
- **Evidence — Cowork:** turn the Audit2/NC-02 matrices into pure-domain invalid-state/property attacks and independently
  review the golden vocabulary mapping.
- **Integration — Codex:** review architecture/invariants, reproduce high-risk mutations, own scope and advance the
  exact-head gate. No self-acceptance by the Build lane.

