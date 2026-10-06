# T05: Fill telemetry. Codex review

*2026-10-06, Africa/Cairo. Reviewed exact head `153cf6d960e15cbbd5cafff521580d5081dac9ee`.*

## Decision: fixes requested

The telemetry schema and coverage are a useful base, and the focused tests pass (`15 passed`). GitHub fast/full checks are green. Nothing from this branch was installed. However, T05 cannot be accepted as observe-only yet because its synchronous file I/O runs inside safety-critical order paths.

## Findings

### P1 — Telemetry can delay protection after an exchange fill

`Engine._fill()` performs `exists`, `getsize`, optional `replace`, file open, JSON encoding, and a write synchronously (`engine.py:448-464`). Catching exceptions prevents an I/O error from escaping, but it does not bound how long a slow or hung filesystem, antivirus scanner, or file lock can block.

This call is placed after Binance has filled an order but before the bot performs the next protective action:

- market entry: `_fill()` at `engine.py:1304` precedes `_create_lot()` at `engine.py:1308`; the lot is persisted at line 1330 and its exchange stop is not created until line 1332;
- partial maker entry: `_fill()` at line 1682 precedes `_create_lot()` at line 1684 and therefore precedes its stop;
- adds/fallbacks: `_fill()` at lines 679-680 precedes `_apply_add()` and the caller's later stop-quantity replacement.

That violates the stated contract that telemetry “never blocks” and does not affect trading. A successful exchange fill must not wait on optional local analytics before the bot records/protects it.

**Required fix:** make the trading path bounded and non-blocking. Enqueue an immutable record with `put_nowait()` into a bounded queue; one background writer owns rotation and disk writes. If the queue is full or unavailable, drop the telemetry record and increment an explicit health counter. Never wait for the writer from an order path. Shut down/flush only outside active order handling, with a bounded timeout.

Add tests that deliberately block the writer's file operation and prove that lot persistence, stop creation/replacement, and close handling still complete without waiting. Also test queue-full/drop behavior.

### P2 — Failed writes create phantom in-memory statistics

`_fill_count(rec)` runs at line 459 before the write at line 462. When the write fails, `/api/status` counts a fill that is absent from `fills.jsonl`; after restart it disappears. The existing failure test proves the order continues, but does not test summary integrity.

**Required fix:** distinguish accepted, persisted, and dropped records, or update the durable summary only after a successful write. Expose at least a dropped/write-error count so missing telemetry is visible. Protect the summary with a lock or immutable snapshot once the writer runs on another thread. Add write-failure and restart assertions.

### P2 — A partial maker record can claim a fallback that never ran

For a partial maker fill, line 1683 sets `fallback=true` before lot creation, the entry-block check, and `_add_qty()`. If lot protection fails, the fallback is blocked, or the fallback order fails, the stored maker record still says the market fallback ran. This contradicts the review contract, which says `fallback` is present only when the fallback actually ran.

**Required fix:** record the maker result independently, then record fallback attempted/blocked/filled only after the relevant decision or exchange result. Add partial-maker tests for a blocked fallback, failed lot creation/protection, and failed fallback order.

### P2 — Rotation makes the advertised restart summary discontinuous

At runtime, statistics accumulate for every fill since process start. `_load_fills()` rebuilds only from the last ~2 MB of the current `fills.jsonl` and ignores `fills.jsonl.1`. Immediately after rotation and restart, most of the previously reported summary can vanish. “Survives restarts” is therefore true only for an unstated partial window.

**Required fix:** define the summary window precisely and reproduce the same window after restart. A simple bounded option is to rebuild from `.1` plus the current file and retain only a documented maximum number/size of records. A durable aggregate with a versioned schema is also acceptable. Add a rotation-plus-restart test.

### P2 — Missing executed quantity is reported as a real full fill

Entry/add/exit hooks use `executedQty or requested_qty`. If Binance returns no `executedQty` or `0`, telemetry records the requested quantity as filled and labels it `filled`, even though the value is unknown. The code correctly leaves a missing `avgPrice` as `null`; quantity should follow the same provenance rule.

**Required fix:** preserve unknown quantity as `null` (and use an `unknown` outcome), or add an explicit `qty_source=inferred` field and do not present it as exchange-confirmed. Add missing/zero `executedQty` tests.

## Acceptance conditions for the next review

1. No filesystem operation or blocking queue operation occurs between an exchange fill and lot persistence, stop creation/replacement, or close-state application.
2. Telemetry loss and writer failures are visible, bounded, and do not produce phantom durable counts.
3. Fallback fields describe what actually happened, including partial-maker failure/block paths.
4. Restart semantics match a documented, tested summary window across rotation.
5. Unknown execution quantities are not invented as confirmed fills.
6. Focused tests, existing order-path tests, and GitHub fast/full checks remain green.

## Scope note

Keep T05 observe-only. Runner profitability, target counterfactuals, and other analysis belong in T05a; `runner_frac` and parent/child execution behavior belong in T09a. No strategy or order behavior should be added while fixing this ticket.
