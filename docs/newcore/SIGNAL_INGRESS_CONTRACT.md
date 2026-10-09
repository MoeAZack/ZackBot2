# SIGNAL-01: versioned signal ingress contract

**Status:** contract foundation only; no network listener and no TradingView execution path is enabled by this ticket.

This contract captures the owner-approved TradingView/relay ideas without widening the current Binance engine gate. It
is shared by future internal-strategy, TradingView and manual signal adapters. Every adapter produces the same strict
signal envelope and enters the same NEWCORE risk, decision, execution, protection and reconciliation path.

Machine-readable contracts:

- `contracts/signal_intent_v1.schema.json`
- `contracts/signal_result_v1.schema.json`

Every adapter runs `parse_strict -> JSON Schema -> check_signal_intent` (`newcore.contracts.signal_v1`). The schema
carries structure and the per-action / per-order-type branches; the semantic validator carries what a JSON Schema
cannot: no binary float token anywhere (so `1700000000000.0` is never an integer), `generated_at_ms < expires_at_ms`
with a TTL ceiling of 1 h (a maximum, not a default: sources choose shorter expiries where their cadence
requires), an order expiry after generation and not after the signal, per-unit numeric bounds,
level method / unit compatibility, one unit per trail, and reason-code registry membership per result status.
Freshness against the trusted clock is mandatory at promotion / use (`check_signal_intent(payload, now_ms=...)`);
`check_signal_shape` is the clock-free variant for audit and replay and never authorizes use. The validator is
standalone: it re-checks every field and refuses unknown fields at every level itself. Results leave the process only
through `encode_result`, which emits a fresh object built from the allowlisted result fields after that check.

## 1. Non-negotiable path

`authenticate -> parse -> freshness/expiry -> durable dedupe -> account alias -> capability check -> risk gateway ->
decision -> order intent -> venue -> exchange truth -> durable result`

No signal source can bypass the risk gateway, ownership proof, protection requirement, account binding or reason-code
registry. Off / Observe / Testnet / Live is trusted application configuration and is deliberately absent from the
payload. A sender cannot select an environment or automation authority.

Protection, exits and reconciliation run before signal intake. If an external scoring/signal feed becomes stale or
unavailable, ZackBot opens nothing new and continues managing existing exposure from its durable local plan and
exchange truth.

## 2. Authentication and privacy

The future HTTP adapter uses an `Authorization` header plus key id, timestamp, nonce and HMAC over the canonical request
bytes. Secrets never appear in the JSON body, URL, logs, screenshots or TradingView message examples. Requests are
bound to an account-alias allowlist and an optional active time window. Rotation supports a bounded overlap; replayed
nonces fail before the signal reaches the engine.

Account aliases are operator-chosen labels. Raw exchange account identifiers stay out of Pine scripts, alert bodies,
screenshots and support bundles. Multi-account routing remains Phase 5; the first adapter may expose only one alias.

## 3. Idempotency and durable truth

`signal_id` is the idempotency key. The intake writes its identity and canonical payload digest durably before any
order may be sent.

- A byte-equivalent retry returns the already-recorded result and sends nothing.
- Reusing a `signal_id` with a different canonical payload rejects as an idempotency conflict and sends nothing.
- A restart reconstructs accepted, rejected, duplicate and held outcomes before allowing new risk.
- Every signal is recorded, including parseable dry runs, rejections and ignored/observe-only signals.

`trade_family_id` groups scale-ins, bounded micro-DCA legs, partial exits and range-basket scalps into one logical trade
campaign. It never permits an add: the risk gateway separately authorizes every member.

## 4. Strict semantics

Unknown fields reject. Each action has its own field set: `enter` needs side, order, risk and stop and never a
`target_ref`; `reduce` needs `target_ref` and `reduce_percent`; `close` carries only its `target_ref`; `modify` needs a
`target_ref` and at least one of stop / target / trail. `target_ref` is a concrete `pos_` / `lot_` (or, for modify,
`int_`) id, never a word such as `all`. `market` orders carry no price or expiry; `limit_post_only` and `stop_market`
require a price. Decimal values are canonical strings, never binary JSON floats, with at most 18 integer and 18
fraction digits; bounds per unit are contract plausibility limits, not risk limits. Stop, target, trail and entry
prices always carry an explicit unit: `price`, `percent`, `ticks` or `atr`; magnitude is never used to guess a unit.

`strategy_id` and `strategy_version` are required on every signal. Strategy behavior changes require a new version.
The recorded decision also captures regime version, risk profile, code/build identity and the exact settings snapshot;
those are trusted engine facts, not sender-controlled overrides.

Absolute-price orders require a venue-resolved concrete symbol. Future TradFi continuous-contract mapping must reject a
priced generic symbol rather than silently applying a price to a different contract month.

## 5. Dry run and results

`dry_run=true` performs authentication, parsing, freshness, account, symbol/capability and risk validation but sends no
venue action and changes no trading state. Its audit record is the only durable side effect.

Every response follows `signal_result_v1` and contains its own `result_id` and a stable machine `reason_code` that is
registered for that status. `accepted` and `validated` name their `decision_id` and are not retryable; `held` and
`duplicate` are not retryable; a duplicate cites `original_result_id` and `original_status` of the stored original,
never itself. Free text is display-only, never drives retry or engine behavior, and is built from fixed templates with
non-secret values; printable ASCII alone is not a secret guarantee, so `encode_result` refuses (never scrubs) text that
names a credential or contains a key-shaped token, and the error never echoes the refused text.

New-entry limits may reject `enter`, but plan/capacity limits never block reduce-risk `reduce`, `close`, protection or
emergency flatten. Unknown ownership may HOLD and require reconciliation; it never guesses an exit target.

## 6. Streaming and UI

The future UI consumes a read model produced by one durable writer. Exchange user-data streams supply fills, orders and
positions; the browser receives bounded server events and periodically reconciles a snapshot. No browser state is
authoritative.

The UI exposes:

- every received signal and its exact accept/reject/hold reason;
- strategy and version scorecards;
- one reconstructed trade family for entries, DCA and scale-outs;
- account alias, protection and reconciliation state;
- after-cost realised/unrealised P&L, drawdown and basket lock state.

## 7. Delivery order

1. **Now:** freeze and test these schemas; use their fields when NEWCORE adds signal attribution.
2. **After the Binance vertical slice:** implement the in-process adapter, durable dedupe and dry-run service.
3. **After long/short and range/scalp validation:** expose the authenticated TradingView HTTP adapter in Observe/Testnet.
4. **After the UI/read-model milestone:** add streaming signal/result views and the setup wizard.
5. **Post-Binance-live:** reuse the contract for additional venues; do not duplicate strategies inside MT5 or brokers.

Mainnet activation remains a separate owner-approved gate.
