# B5 ADR inputs — NEWCORE first contract (PRE-STAGE draft, Claude Code)

Base evidence: `origin/master` @ `cff3f88` (AUD-07 golden gate) + PR #34 head `6150ef4` (plan v3.1 + Audit2 integration).
Draft only. Each item gives options, a recommendation (**R**) and the legacy evidence behind it. Codex decides.

---

## 1. State / action model

**D1.1 Mutation model**
- (a) Mutable dict state, as in legacy `Engine.state` (lots/unconfirmed/resting/pending/grids/orphans dicts mutated in place, saved "when changed").
- (b) Frozen records + pure transitions: `decide(view) -> Decision[]`, `apply(portfolio, event) -> portfolio`; the shell executes intents.
- (c) Full event sourcing: state = fold(all events); snapshots are only a cache.
- **R: (b) with an append-only event log (NC-02 snapshot + events).** The snapshot is authoritative after compaction, and the
  events replay deterministically. Evidence: Audit2 reproduced "a resolved quantity-only pending add updated memory but left the disk
  with the old quantity and pending record". Legacy `_resolve_pending` mutates the lot and relies on `manage()`'s
  `changed` flag, which only counts lot-count changes, so nothing saves it. Under (b), a result is a durable event before it is
  applied, so memory can never run ahead of disk.

**D1.2 Durability order (the WAL rule, carried over from AUD-05 r2)**
- `IntentRecorded` is durable before the send. `ResultObserved` is durable before it is applied. A failed append means nothing is sent or applied.
- This is identical to legacy `_save_wal()` (strict) for entries, adds, provisional stops and maker placements. The legacy close path
  (`_market_close`) has **no** WAL before the send; it only records `pending` after an `AmbiguousOrder`. **R:** every
  `OrderIntent` (close/reduce/stop/cancel included) gets the same WAL rule. Closing with no WAL is how a crash between send and
  record loses ownership of a close.

**D1.3 Action vocabulary and priority**
- **R:** `Action` enum ordered by plan rule 7: PROTECT > FLATTEN > CLOSE > REDUCE > HALT > PAUSE > CANCEL_ENTRY > RECONCILE >
  ADD > ENTER > HOLD/SKIP. The scheduler (NC-05) and the order budget (NC-04) consume `Decision.priority`. Under budget
  pressure an ENTER can never pre-empt a PROTECT.

**D1.4 Time representation**
- (a) floats from `time.time()` plus ISO strings (legacy: both mixed on a lot: `t`, `opened`, `stop_confirmed_t`).
- (b) `int` UTC epoch milliseconds in every record; ISO/Cairo only at the display edge; a monotonic clock is never persisted.
- **R: (b).** It is exact and sortable, has no float drift, and the AUD-12a injectable clock supplies it. (The sketch uses float/str for brevity.)

**D1.5 Numbers**
- (a) float, as in legacy, which needs `_qty_tol`, `near()` tolerances, `_rd` re-rounding and 1e-12 epsilons everywhere.
- (b) `Decimal`, validated against the instrument step/tick, with JSON as decimal strings (the golden pack already uses decimal strings).
- (c) integer step/tick counts (`qty_steps:int`).
- **R: (b) at the record level.** Hot math (indicators, sizing) may use float internally but quantizes on entry to a record.
  Measured on the sketch: decode+validate of a 1-lot portfolio takes ~126 µs with cached type hints (Py 3.14). (c) is the fastest and
  most exact option, but it couples every record to an `Instrument` and makes the golden/exchange I/O noisy.

---

## 2. Module boundaries (NC-01..09)

**R: one package `zackcore/`, with stdlib-only `domain` at the root of the dependency graph. Edges are enforced by a test (D2.2).**

| Module | Ticket | Owns | May import |
|---|---|---|---|
| `domain` | NC-01 | records, enums, `ReasonCode`, invariants, codec (`encode/decode_document`, `FutureSchema`/`InvalidRecord`) | stdlib only |
| `store` | NC-02 | per-account snapshot + event log, schema migration, recovery classification (future / invalid / missing / ok), evidence preservation | domain |
| `venue` | NC-03 | exchange adapters → `OrderResult`, `ExchangePosition`, `OpenOrderRow`; idempotent submit/query/cancel by client id; classic/algo normalization | domain |
| `account` | NC-04 | `AccountContext` worker, order/weight budget, 418/429 policy, incidents per account | domain, store, venue |
| `runtime` | NC-05 | monotonic scheduler, cycle/manage/guards, immutable read model (UI snapshot) | all above |
| `risk` | NC-06 | the single gateway: proposal → `Decision(ENTER\|SKIP)` with sizing | domain |
| `manage` | NC-07 | pure management transitions: (lot, market view, plan) → `Decision[]` | domain |
| `sim` | NC-08 | simulated venue + replay/backtest adapters producing the same events | domain, venue protocol |
| `observe` | NC-09 | structured events/incidents sink; never blocks protection | domain |

- **D2.1** `risk` and `manage` are pure. They take no store/venue/clock objects, only values. This is what makes the live/replay/backtest parity of NC-08 cheap.
- **D2.2 Boundary enforcement.** (a) review only; (b) an `ast` import-graph test in `tests/` (stdlib); (c) the `import-linter` dependency. **R: (b).**
- **D2.3** The legacy modules are never imported by `zackcore`, except by the golden `legacy_*` adapters (which live in `tests/golden`).

---

## 3. Account identity

Legacy: `install.json.account = {mode: paper|live, base: <url>, key: pbkdf2(API_KEY)[:16]}` (`account_fingerprint`).
A rotated API key therefore looks like a different account (install mismatch → paused). There is one data folder per account (AUD-00).

**D3.1 Identity**
- (a) The key digest is the identity (legacy).
- (b) The exchange-reported account uid is the identity. It is not reliably available on USD-M futures/testnet, and it is unknown for sim/backtest.
- (c) Opaque `AccountId = acct_<uuid4>` assigned at creation, plus a separate `AccountBinding {venue, environment, base_url, key_digest, exchange_uid?}` as evidence.
- **R: (c).** Every record carries `account_id`. Positions/lots/intents of another account are an invariant failure in `Portfolio`.
  Key rotation is a binding change on the same `AccountId`: it needs an explicit typed confirmation (OPS-UI-01) and leaves
  entries **paused**. `environment` is part of the binding. A portfolio bound to `testnet` can never be loaded under a
  `mainnet` binding, and nothing in NC-01 can construct that transition (it is a release-gate action).

**D3.2 Store key.** **R:** `store/<account_id>/…`. The data folder may hold several accounts (NC-04 two-account proof).
The AUD-00 single-instance lock moves to a per-`account_id` lock.

---

## 4. Reason-code ownership

Legacy: free text is generated at dozens of sites in `engine.py`, then classified after the fact by `trade_audit._EXACT/_PREFIX/_WRAP`
(substring-prefix rules; unknown text → `other/other`). Exit reasons are bare strings (`'stop'`, `'take_profit_1'`, …). The golden
pack has its own `EXIT_CODES`, and `legacy_engine.REASON` maps between them (`flatten`, `resync`, `stop_failed` and `basket_tp_part`
come out as `UNMAPPED:`).

**D4.1 Where codes live**
- (a) A per-module enum in each owning module.
- (b) One central `ReasonCode` `StrEnum` in `domain`, with values `"<stage>.<code>"`.
- (c) Free strings plus a registry.
- **R: (b).** Values are append-only: never renamed or reused, only deprecated. A new code is a `domain` change reviewed with the ticket that
  emits it. `stage` is derived from the value. Display text lives in a separate table keyed by code (in UI/observe), never parsed back.

**D4.2 Decision contract.** **R:** every `Decision` carries exactly one primary `ReasonCode` plus a sanitized `detail ≤ 160`
chars (the same cap as legacy `reason_info` detail). A SKIP must use a gate stage. CLOSE/REDUCE must use `exit.*`.

**D4.3 Golden projection.** **R:** `GOLDEN_EXIT: ReasonCode → zb-golden EXIT_CODE | None` lives in `domain`. NEWCORE keeps
`exit.stop_crossed` (bot market close after price crossed a trailing level) distinct from `exit.stop` (exchange stop fill). The
golden trace projects both to `STOP_HIT`. `exit.resync`, `exit.stop_failed` and `exit.manual` have no golden code in v1, so AUD-08 decides on
adding `RESYNC`/`STOP_FAILED`.

**D4.4 Legacy text mapping.** **R:** `trade_audit._EXACT/_PREFIX/_WRAP` become a one-way seed table, `legacy_reason →
ReasonCode`, inside `tests/golden/goldenlib/adapters` (temporary adapter, deleted at Checkpoint C). NEWCORE never classifies text.

---

## 5. Legacy deletion map (Checkpoint C dispositions, proposed)

| Legacy | Size | Surviving contract → NEWCORE owner | Disposition |
|---|---:|---|---|
| `engine.py` `Engine` | 4,625 | AUD-00..05 invariants → NC-01 types, NC-02 store, NC-03 order machine, NC-07 transitions | **delete** after NC-05/07 parity |
| `engine.validate_state` / `_validate_*` / `ownership_present` | — | Become NC-01 constructor invariants (see domain draft). Legacy JSON shapes become negative fixtures. | **delete** |
| `engine` `install.json` + `account_fingerprint` | — | `AccountBinding` (D3) | **delete** (keep the PBKDF2 digest formula) |
| `engine` `_order_result` / `_exec_of` / `ORDER_FINAL` | — | `OrderResult` phase/evidence (NC-01) + NC-03 | **delete** after NC-03 |
| `engine` stop verifier (`_verify_symbol`, `_protects`, `_take_cover`, `BOT_STOP_CID_RE`) | — | `Protection` status machine (NC-01) + NC-03/NC-05 verifier | **delete**; port the formulas with tests |
| `trade_audit.py` | 1,604 | `_EXACT` seeds `ReasonCode`; MFE/MAE/give-back/counterfactuals → NC-09 / research | `reason_info` **temporary adapter**, rest **research-only** |
| `grid.py` | — | none (plan C6: do not port Grid). Also bypasses AUD-03: `_open` books `executedQty` without `_exec_of`. | **delete** |
| `ai_filter.py` | 46 | none (C6). It also fails *open*: "the trade is taken" if the call fails. | **delete** |
| `feasibility.py`, `exchange_rules.py` | 375 / 134 | `size_check`, `risk_qty`, `leaves_dust`, rule snapshots → NC-06 / VENUE-01 | **still required** until ported with their tests |
| `binance_client.py` | — | `is_transient`, `retry_after`, `new_cid` shape, algo/classic tags → NC-03 | **delete** after NC-03 |
| `instance.py` | 188 | AUD-00 single instance → per-account lock (NC-04) | **still required** until NC-04 |
| `replay.py`, `backtest.py` | 212 / 663 | the golden `legacy_engine` / `legacy_backtest` adapters | **temporary adapter** until NC-08 strict parity, then delete |
| `strategies.py`, `lab.py`, `research_*.py`, `market_data.py`, `market_collector.py` | — | STRAT-00 / DATA-01 inputs | **research-only** |
| `app.py`, `test_app_ui.py`, `telegram_ctl.py` | 1,609 / — / 627 | replacement UI (OPS-UI-01), NC-09 alerts | **delete** with the replacement UI |
| `verify.py`, `verify_ci.py` | — | the CI plan | **still required**; it gains `zackcore` slices |
| `tests/golden/**` | — | the parity gate | **still required**; `newcore_sim` / `newcore_live_sim` adapters arrive in NC-07/08 |

**D5.1 Legacy state import.** (a) none, since replacement-first means testnet positions are disposable (plan §11); (b) a one-shot importer
from `state.json` v1. **R: (a)** NEWCORE starts flat on testnet. Legacy state files are used only as negative/characterization fixtures.
