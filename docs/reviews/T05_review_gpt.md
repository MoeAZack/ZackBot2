# T05: Fill telemetry. Codex review

*2026-10-06, Africa/Cairo. Reviewed exact head `153cf6d960e15cbbd5cafff521580d5081dac9ee`.*

*Round 2 reviewed exact fix head `2d0e985bd3f43d916f6f9984c2dde9bcaabeceed`.*

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

---

## Round 2 review — fixes still requested

The original P1 is substantially improved: order paths now use `put_nowait`, the writer owns disk I/O, persisted-only statistics remove phantom counts, the two-file window survives rotation/restart, missing execution quantities are `unknown`, and fallback decisions are recorded after the decision. The focused Windows suite passes: **28 passed in 1.27 s**.

Three correctness/lifecycle gaps remain.

### P1 — A timed-out handover starts a second writer on the same files

`_fill_stop()` waits up to two seconds, tries to enqueue a sentinel, and returns the flush result (`engine.py:522-528`). It never joins or proves the thread ended. `App.start_engine()` ignores that result and immediately constructs a new `Engine`, whose constructor starts another writer (`app.py:451-460`). If the old writer is blocked by a slow disk or antivirus—the exact failure this design is meant to tolerate—both writers remain alive against the same `fills.jsonl` and rotation target.

This was reproduced on the reviewed head by blocking `_fill_write`: `_fill_stop(0.05)` returned `False`, then a replacement engine was constructed; both `old_writer_alive` and `new_writer_alive` were `True`. If the queue is full, even the sentinel's `put_nowait` can fail silently, so the old writer may never stop at all.

**Required fix:** make writer ownership exclusive and explicit. A stop must first prevent new accepts, guarantee a stop signal, and join the writer. If it cannot prove termination within the bounded handover, `start_engine` must not start another writer for the same path. A process-scoped single writer is preferable; alternatively start the replacement with telemetry disabled/drop-only and expose the handover failure until ownership is safe. The caller must check the result.

Add blocked-write and full-queue settings-restart tests that assert there is never more than one thread capable of writing/rotating the file, the restart remains bounded, and a failed handover is visible rather than ignored.

### P2 — `fallback=true` still means “call returned,” not “fill confirmed”

The updated review contract says `fallback` is true only when the fallback order filled. However, `_add_qty()` and `_market_entry()` retain the trading fallback to requested quantity when `executedQty` is missing. Their telemetry record correctly says `outcome=unknown`, but their Boolean return still causes the parent maker record to set `fallback=true`.

Reproduction on the reviewed head, using an unfilled maker whose fallback response omitted `executedQty`, produced:

`entry_fallback: outcome=unknown, fallback=true`; `entry_maker: outcome=unfilled, fallback=true`.

Ambiguous/lost-answer orders are similarly labelled `fallback_failed`, although they are pending reconciliation rather than confirmed failures.

**Required fix:** separate `fallback_attempted`, `fallback_confirmed`, `fallback_pending` and `fallback_failed` (or equivalent exact states). A missing/zero execution quantity must never set the confirmed flag. An `AmbiguousOrder` must be pending/unknown, not failed. Add partial and unfilled maker tests for missing quantity and ambiguous fallback responses. If reconcile completion remains a later ticket, retain a truthful pending state now.

### P2 — One valid but malformed JSON line can break `/api/status`

`_load_fills()` appends any JSON value to `_fill_win`; `fill_summary()` assumes every item is a dictionary and performs `rec.get`. A local `fills.jsonl` containing the valid JSON line `[]` makes `fill_summary()` raise `AttributeError: 'list' object has no attribute 'get'`. Malformed field types can likewise break `sum`/`max`.

Telemetry is optional and must not take down health/status after corruption, manual inspection, or a schema change.

**Required fix:** validate and normalize loaded records before admitting them to the window, skip/count invalid records, and keep summary aggregation defensive per record. Add scalar/list JSON, wrong field types, truncated line and mixed old/new-schema restart tests. Expose an `invalid_records` health counter.

### P2 — Writer lifetime is not closed for tests, replays, or failed construction

Every `Engine` constructor starts a permanent daemon thread. Its bound target strongly retains the engine, so the weak-reference `atexit` callback does not make unused engines collectible. The suite has at least 99 `mk_engine()` call sites, and replay/test engines do not close their writers. If construction fails after `_fill_init()` (for example in later initialization), the writer also leaks because no caller owns the partial engine.

**Required fix:** add one idempotent lifecycle method/context boundary and call it in app replacement, replay `finally`, and test fixtures. Prefer lazy or process-scoped ownership so engines that never emit telemetry do not consume threads. Test constructor failure and repeated engine creation/replacement for stable thread count.

## Round 2 acceptance conditions

1. A blocked/full old writer can never overlap a replacement writer on the same telemetry files.
2. Fallback state distinguishes attempted, confirmed, pending/ambiguous and failed; unknown quantity is never confirmed.
3. Any malformed or older JSONL record is skipped/countable and cannot break status.
4. Engine/replay/test lifecycle leaves no telemetry threads behind, including construction failure.
5. The 28 focused tests, existing order-path tests, full suite and fresh GitHub fast/full checks pass after rebasing onto protected `master`.


---

## Round 3 review — one lifecycle race remains

*Reviewed exact fix head `ba8381049e30ad33c70b8ac1d3ce3eeda1a879ca` on 2026-10-06 (Africa/Cairo).*

The round-two changes correctly establish one process-wide writer per telemetry path, retain a stuck writer in visible `closing` state, distinguish attempted/confirmed/pending/failed fallbacks, reject malformed history records, start writers lazily, and close replay/test writers. The four round-two findings are otherwise resolved. The push fast check passed in 4m30s; the PR full check was still running when this review was written. Nothing was installed or run against Binance.

### P2 — `emit()` can accept a record after `close()` has already stopped the writer

`FillWriter.emit()` checks `closing` and starts the thread while holding `self.lock`, but releases that lock before `q.put_nowait(rec)` and before incrementing `accepted` (`engine.py:63-78`). `FillWriter.close()` can acquire the lock in that gap, set `closing`, observe an empty queue, set the stop event, join the now-idle thread, and return success. The suspended emitter can then resume, enqueue the record on the stopped/orphaned writer, and count it as accepted. The record is never written, is not counted as dropped or a write error, and a later `fill_writer(path)` may replace the apparently stopped writer while its abandoned queue still contains work.

This does not block or alter an order, but it breaks the lifecycle guarantee introduced by this round and makes the telemetry health counters untruthful at shutdown/replay cleanup.

**Required fix:** make the closing check and queue admission one atomic operation under `self.lock` (including the `accepted`/`dropped` counter update). `close()` must not be able to mark the writer closing between the successful admission decision and `put_nowait`. Keep the put non-blocking. Add a deterministic race test that pauses an emitter immediately before queue admission, calls `close()`, then releases the emitter and proves either (a) the admitted record is persisted before close succeeds, or (b) it is rejected and counted as dropped; it must never remain accepted in a dead writer's queue.

After that narrow fix, rerun `tests/test_fills.py` plus the fast check. A new full 25-minute run is unnecessary if the only code change is this lock-boundary fix and the new focused test; the already-running full result on `ba83810` can remain supporting evidence, followed by one final full run only when T05 is otherwise ready to merge.
