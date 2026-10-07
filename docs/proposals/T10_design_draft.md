# T10: per-account order budget and priority queue (design draft)

Status: draft proposal for Codex placement · Class E (execution) · Prepared by Claude, 2026-10-07. Based on master plus T05b, T05a and the T06–T09 core stack (local `core-v5`).

## 1. Scope

**In scope**
- Rate-limit accounting for each account. Binance response headers are the source of truth; conservative local token buckets are the fallback.
- Priority-based admission at every order call site in `engine.py` and `grid.py`.
- Correct 429/418 handling in `binance_client.Futures._req`. The minimal fix is the separate small PR *TRATE*: a 418 ban blocks all requests for the uncut Retry-After, and a 429 sleeps at most 2 s inside `_req`.
- Panel status fields and incidents.
- A synthetic testnet fault for the 429 path.

**Out of scope**
- An async order executor or a durable queue. The engine stays one synchronous loop under `engine.lock`.
- Risk rules such as StoplossGuard, CooldownPeriod and TWEL. These belong in their own ticket, proposed as **T10b**.
- Binance mainnet quantitative trading rules (unfilled and cancel ratios). They are covered under Risks only.

**Shape of the design.** There is no background executor. Each order goes through a synchronous per-account admission check before it is sent. Every class can spend budget only down to a reserve kept for higher classes. Work that gets deferred is retried by loops the engine already has: `stop_dirty`, `add_blocked`, orphans, grid op and maker `between`. Nothing new has to survive a crash.

## 2. Binance facts to verify on testnet (read-only header capture first)

| # | Fact (USD-M, from knowledge) | How to verify |
|---|---|---|
| F1 | REQUEST_WEIGHT is 2400/min per IP. ORDERS is 1200/min and 300/10 s per account. All are listed in `exchangeInfo.rateLimits`. | Log the parsed `rateLimits` at `connect()`. Testnet may differ. |
| F2 | `X-MBX-USED-WEIGHT-1M` is on every response. `X-MBX-ORDER-COUNT-10S` and `-1M` are on order responses. | Header ring (T10-1), checked per endpoint, including `/fapi/v1/algoOrder`. |
| F3 | A new order costs 0 IP weight and 1 order. A cancel costs 1 weight and may not count as an order. | Compare header deltas around a single cancel. |
| F4 | Algo or conditional stop orders count toward ORDERS. | Header delta on an algo stop. |
| F5 | Weights: `positionRisk` 5, `openOrders` 1 with a symbol and 40 without, `account` 5, `klines` up to 10, `openAlgoOrders` unknown. | Serialized weight deltas. |
| F6 | Windows are fixed, not sliding. The design assumes sliding, which is safe either way. | Watch the resets. |
| F7 | 429 means a limit was hit, with Retry-After. Ignoring it leads to a 418 IP ban lasting from 2 min to 3 days. Codes: -1003 is IP weight, -1015 is account orders. | Never trigger this for real. Use the synthetic `rate_limit:<s>:<scope>` fault. |
| F8 | The lead portfolio API allows 20 requests per 10 s (ROADMAP A). | Open question until the Phase 7 rehearsal. |

## 3. Priority classes

| Class | Name | Typical call sites |
|---|---|---|
| P0a | PROTECT_MISSING / EMERGENCY | First stop placement, `stop_dirty` restore, stop resize after a size **increase**, `_resolve_pending` stop, `stop_crossed`/`force_close`/`stop_failed` closes, `flatten` (manual or PEAK_DD) |
| P0b | PROTECT_UPDATE (old stop still live) | Breakeven/trail/runner moves, `_tighten_all`, resync stop after a size decrease, manual `move_stop` |
| P1 | EXIT / reduce | Exit signals, time exits, tp1/ladder/tp_r/basket TP, grid range exit |
| P2 | CANCEL / orphan | `_cancel_or_park`, orphan retries, maker cancels, `cancel_all` |
| P3 | ENTRY / ADD | Market/maker entries, maker fallback, trailing entries, DCA and pyramid adds, manual trades, plus pre-entry leverage, margin and exposure-proof reads |
| P4 | GRID | Grid opens, grid adds, grid TP cycling |
| P5 | READ (non-critical) | Status reads, equity record, reconcile `open_stop_tags` |

**Rules**
- Confirmation work is pre-authorized under the class of the order it confirms: `get_order` lookups, the single resend and critical position reads. Denying it would turn a known order into an ambiguous one.
- A static AST test requires every order call site to carry an explicit class.
- An unclassified call is classified from its params: STOP_MARKET or close-side → P0a, open → P3.

## 4. Budget accounting

- **IP weight bucket:** one per host, held in a process registry and shared by all accounts on that IP.
- **Order buckets:** one per account (10 s and 1 min), kept on the account's `Futures` client.
- **Used count:** the larger of the local sliding count and the header value, while the header is fresh (younger than 15 s). A header that is malformed or unknown puts the bucket in RED for P3–P5.
- **Local charge:** charged before sending, from a static `WEIGHTS` table, then corrected from the response header.
- **Cap:** `floor(min(default, exchangeInfo value) × SAFETY)`. It never rises above the defaults.
- **Admission function:** pure, in `core/budget.py`: `decide(cls, state, now) -> Admit | Defer(code, retry_in)`. A class may spend only while `remaining > reserve[cls] × cap`.
  - P0a has reserve 0 and may overdraw locally up to 0.9 × the exchange limit.
  - P0b is denied only by an exchange cooldown.

## 5. Behaviour under pressure

| State | Trigger | Admitted |
|---|---|---|
| GREEN | everything below its P3 reserve | all |
| AMBER | below 55 % remaining | P0–P3 (P4 shed) |
| RED | below 40 % remaining, or header unknown/stale under load | P0–P2 (P3 deferred or shed) |
| COOLDOWN | 429 until Retry-After (default 10 s, backoff capped at 60 s) | one P0a probe per pass per account, most urgent first |
| BANNED | 418 until the uncut Retry-After (at most 3 days) | nothing from that IP; one SOS notification; reads fail fast |

- **Protection is never delayed by local accounting.** Only a 429 or 418 from the exchange can delay it, and stop-first replacement keeps the old stop live in the meantime.
- **Entries are shed first.** Each shed entry is recorded via `miss()` with code `budget_shed`. A shed maker entry goes back to `between`; a shed manual entry raises a clear error.
- **DCA and pyramid adds are deferred, never lost** (`add_blocked = 'order budget: deferred'`).
- **The grid budget check runs before `_op()` saves state.**
- **`BudgetDeferred` is raised before any side effect:** no client id, no state change, no I/O. It is never ambiguous and never counts as a manage failure.
- **No in-call sleeps for P2–P5.** P0 and P1 get at most one retry inside the call, and only for a wait of 2 s or less.

## 6. Interaction with T05b and the idempotent order path

**T05b outage circuit**
- `ExchangeHealth` stays the outage circuit and the budget owns rate state.
- A 429 or 418 on a read still calls `health.fail(read=True)` and also calls `budget.on_limit(...)`.
- The 418 ban window uses the uncut Retry-After.
- After a recovery, the stop re-read runs under a pre-authorized ticket, so a RED budget can't hold up recovery.

**Idempotent order path**
- `_order` keeps client-id idempotency. Admission is decided once, before the first POST.
- A resend is charged but never denied.
- A 429 on a POST is a definite rejection, not an ambiguous order.

## 7. Per-account isolation (Phase 5 prep)

- **Budget per account:** keyed by an API-key hash, later by the `AccountContext` id.
- **Shared IP weight:** split fairly between accounts, with a P0 slice per account that other accounts can't borrow.
- **Limit of isolation:** a 418 ban applies to the whole IP. Decision rule B5 (one account's failure never delays another's stops) can only be fully met with one egress IP per account; see Q3.

## 8. Observability

**`health.order_budget` fields**
- state and limits (with their source);
- IP weight and order counts, each as header / local / cap;
- `cooldown_until` and `ban_until`;
- `last_limit`;
- deferred and shed counts per class, and the oldest deferral per class;
- `p0_probes`.

**Engine state goes `degraded` when** the IP is banned, a P0b has been deferred for more than 30 s, or a P1 for more than 60 s.

**Incidents:** `rate-limit` (coalesced), `rate-ban` (one SOS, then re-armed) and `budget-pressure` (RED for more than 60 s).

**Reason codes:** `budget_shed`, `budget_deferred`, `rate_cooldown` and `rate_ban`.

## 9. Defaults (constants, no user setting)

| Setting | Value |
|---|---|
| SAFETY | 0.6 (180/10 s, 720/min, IP weight 1440/min) |
| Lead profile | 20/10 s with a local cap of 16 (via `AccountContext`, later) |
| Reserves (share of cap kept for higher classes) | P0a 0, P0b 0.05, P1 0.10, P2 0.20, P3 0.40, P4 0.55, P5 0.30 |
| P0 overdraft | 0.9 × exchange limit |
| Header stale after | 15 s |
| Cooldown default | 10 s |
| Ban maximum | 3 days |
| Testnet-only env | `ZB_TESTNET_FAULTS=rate_limit:<s>[:ip\|acct\|ban]` and `ZB_TESTNET_BUDGET=<orders_per_10s>` |

## 10. Tests (new `tests/test_order_budget.py`, fake clock, extended FakeX with header replay)

1. **Storm, 300 intents in 10 s:** every P0a admitted in arrival order; ≤ 0.9 × 300 sent; P4 shed before P3, P3 before P2; every deferral and shed recorded.
2. **Lead 20/10 s:**
   - flattening 12 lots sends all 12 closes;
   - the stop cancels are P2 and may defer to the orphan list;
   - DCA adds are deferred and entries are shed;
   - everything recovers after 10 s.
3. **429 -1015:** COOLDOWN; exactly one P0a probe per pass; `time.sleep` is never called for P2–P5.
4. **418 with Retry-After 7200:** `ban_until = now + 7200`; zero calls before then; one incident and one notification.
5. **Priority inversion:**
   - an orphan cancel gets a 429 inside `manage`, with no sleep while holding the lock;
   - a `stop_dirty` restore in the same pass is still sent;
   - the panel's `move_stop` gets the lock within one pass.
6. **Header beats local count:** a header of 2300/2400 denies P3–P5 and still admits P0a.
7. **Bad headers** (`abc`, `-5`, `nan`, `1e99`) are ignored and treated as unknown, which means RED for P3+. Nothing ever raises.
8. **Window expiry** happens exactly at 10 s and 60 s, never early.
9. **Two accounts:** one account exhausting its budget doesn't affect the other's P0 or P3.
10. **No side effects on deferral** across the grid, maker and market entry paths.
11. **Pre-authorized confirmations** complete even after the budget is exhausted.
12. **`rateLimits` parsing:** lower values are used; higher or malformed values fall back to the defaults.
13. **No change when there is no pressure:** FakeX call logs are byte-identical and golden fingerprints are unchanged.
14. **Static test:** every order call has an explicit class.
15. **The synthetic fault is inert** unless the client is on TESTNET and the flag is set.

**Mutations that must be killed**
- the reserve comparison inverted;
- the P0a bypass removed;
- the 418 clamp to 60 s restored;
- the header ignored;
- early refill;
- the budget checked after side effects;
- confirmations denied;
- one global budget instead of per account;
- a header parse error raising;
- the in-call sleep put back for P2;
- a deferral counted as a manage failure;
- an add-resize misclassified as P0b.

## 11. PR split

| PR | Content | Behaviour |
|---|---|---|
| T10-1 | Header capture ring, `rateLimits` parse, `WEIGHTS` table, observe-only panel fields, synthetic fault, 24 h testnet observation, facts report | none |
| T10-2 | Pure `core/budget.py`, the adapter, and tests 1, 6–9 and 12, wired in shadow mode | none |
| T10-3 | Remaining `_req` 429/418 refinements on top of TRATE (`cls` parameter, scope classification); tests 3–5 | E |
| T10-4 | Enforcement at call sites, reason codes, incidents; tests 2, 10, 11, 13, 14; testnet canary with `ZB_TESTNET_BUDGET=20` | E |
| T10-5 (optional) | Reorder the `manage()` pass so the P0 restore runs before orphan cancels and maker polls | E (call order only) |

## 12. Risks

- **A protective action misclassified as deferrable.** Covered by the static test, the params fallback, and the rule that an add-resize is P0a.
- **Testnet limits differ from mainnet.** Re-verify F1–F7 read-only before Phase 6.
- **The lead limit of 20/10 s with stop-first replacement** means each stop move costs 2 requests. Copy profiles need fewer stop moves.
- **A shared IP breaks B5 isolation** (Q3).
- **Shed entries create live-vs-backtest divergence** under pressure. They are recorded and need a replay scenario.
- **Maker repricing is cancel-heavy.** Monitor it in T10-1 for the mainnet ratio rules.

## 13. Open questions for Codex

- **Q1:** Should a shed entry instead be deferred briefly, for example 1 pass or at most 30 s?
- **Q2:** Should P0b also get a probe during COOLDOWN?
- **Q3:** Should Phase 5 use one egress IP per account or master, or is a fair-share P0 slice enough?
- **Q4:** Should T10-5 (the pass reorder) be included?
- **Q5:** Does the lead limit count cancels and reads? The design assumes yes until proven otherwise.
- **Q6:** Should the IP bucket key include the egress IP?
