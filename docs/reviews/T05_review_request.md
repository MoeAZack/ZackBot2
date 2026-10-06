# T05: Fill telemetry. Review request

## Current state

| Item | Value |
|---|---|
| Branch | `t05-fill-telemetry`, from protected `master` f9778ca. **Pipeline mode** (owner, 2026-10-06): runs alongside the T03b runtime gate; no shared files with T03b |
| Head for review | the PR head. The full SHA is in the FIXED FOR CODEX comment (round 1 reviewed 153cf6d → Codex 3633ce6; round 2 reviewed 2d0e985 → Codex ac2afe4) |
| Class | **Observe only.** Nothing traded changes: same decisions, orders, sizes, stops and exchange calls |
| Runtime | Nothing installed. The telemetry becomes active with the next approved install |

## The change

Every fill the bot sends itself is recorded as one JSON line in `%LOCALAPPDATA%\ZackBot\fills.jsonl` (rotated to `fills.jsonl.1` at 5 MB). Order paths only build the record and `put_nowait` it into a bounded queue (1000). Since round 2, ONE process-wide `FillWriter` per file does every file operation: it is shared by engines replaced in a settings restart, and its thread starts lazily (see the dispositions):

| Field | Meaning |
|---|---|
| `kind` | `entry_market`, `entry_maker`, `entry_fallback`, `pyramid_add`, `safety_order`, `exit` |
| `expected` / `actual` | The price the bot decided at (the mark used for sizing, the first posted maker price, the exit mark) and Binance's `avgPrice`. A missing `avgPrice` is recorded as `null` and never invented |
| `slip_bps` | (actual - expected) / expected × 10 000, signed so that **+ = worse for us** (buying higher or selling lower) |
| `qty_req` / `qty_fill` / `outcome` | Requested vs filled; `filled`, `partial`, `unfilled`, or `unknown` (Binance gave no executedQty: `qty_fill` is `null`, never the requested quantity) |
| `wait_s` | Seconds from send (maker: from the first posting) to the result |
| extras | `signal_px`, `maker_tries`, `reason` (exit or add reason), `manual`, `fallback_order` (this order WAS the market fallback). Maker records state exactly what happened to the fallback: `fallback_attempted` (an order was sent), `fallback_confirmed` (only when its own record shows an executed quantity), `fallback_unconfirmed` (answered without a quantity), `fallback_pending` (answer lost, reconcile decides), `fallback_failed` (order error), `fallback_blocked`, `fallback_skipped`, `fallback_note` |

- **Summary:** `/api/status` → `health.fills = {by_kind: {n, partial, unfilled, unknown, fallback (= confirmed fallbacks), slip_avg_bps, slip_worst_bps, wait_avg_s}, recent: [last 10], telemetry: {accepted, persisted, dropped, write_errors, invalid_records, queued, window, in_window, state: idle|running|closing}}`.
  - **Window:** the last 5000 records **written to disk** (`FILL_WINDOW`). It is rebuilt at start from `fills.jsonl.1` + `fills.jsonl`. Rotation happens at 5 MB, about 16k records, so the two files always hold the window and a restart shows the same summary.
  - **Counters:** since process start.
- **Hooks:**
  - `_market_entry` (market entries and the market fallback after an unfilled maker entry);
  - `_maker_finalize` (maker filled, partial, or unfilled with fallback or blocked fallback);
  - `_add_qty` (pyramid adds, safety orders, the maker remainder fallback);
  - `_market_close` (exits).
- **Safety:**
  - No file I/O and no blocking queue operation happen in an order path; a full queue drops the record and counts it.
  - Building a record catches everything.
  - Dry mode records nothing.
  - A settings restart reuses the same writer (no handover).
  - A writer that cannot be proven stopped stays registered as `closing` (drop-only, counted), and no second writer starts next to it.
  - `close_fill_writer(s)` (atexit, replay `finally`, test fixture) stops accepting, flushes (bounded), signals through an Event (no queue space needed) and joins.
  - Loaded lines are validated: non-dicts and wrong types are skipped and counted as `invalid_records`, and the summary is defensive per record.

Not covered (known limits, proposed for later):
- exchange-side stop fills: Binance fills these, so their expected-vs-actual needs the stop price plus the fill read from reconcile;
- fills resolved from a lost answer (`pending` → reconcile);
- backtest calibration from this data (roadmap "fill telemetry calibrated").

## Tests (`tests/test_fills.py`: 45 in the runner, parametrized cases included: 15 original, 13 from round 1, 17 from round 2)

- long and short market entries: adverse slippage is positive on both sides;
- exit against the decision mark;
- a missing avgPrice gives null, not an invented value;
- maker full fill measured against the posted price;
- maker partial plus market fallback (both records, quantities add up to the lot);
- maker unfilled with fallback;
- maker unfilled without fallback;
- blocked fallback recorded as blocked;
- a telemetry failure never blocks a trade;
- identical trading with and without a working telemetry file;
- the summary survives a restart;
- dry mode records nothing;
- the status exposes a JSON-serializable summary;
- no negative zero.

Round 1 additions:
- a hung writer, blocking on every write, never delays lot persistence, stop creation, an add, stop replacement or a close, and the records are queued, not lost;
- a full queue drops without waiting and counts it;
- failed writes leave no phantom counts and show write_errors, and the restart agrees;
- the same window before and after a restart across rotation;
- missing or zero executedQty (None, '0', '') gives unknown with trading unchanged;
- partial maker with a blocked fallback, a failed fallback order, or no lot: each recorded accurately, never as `fallback`;
- unfilled maker whose market fallback fails is recorded as failed;
- static guard: no file I/O or blocking put in `_fill`, `_fill_rec` or `_fill_emit`;
- engine replacement hands the file over cleanly (writer ended; app.py calls it under the old lock).

**Mutation proof (round 0):** each of these breaks at least one test:
- slip sign ignoring the side;
- an invented actual price;
- telemetry exceptions escaping;
- the blocked-fallback flag;
- no restart reload;
- the maker expectation taken from the signal instead of the posted price.

**Mutation proof (round 2): 10/10 caught.**
- no shared writer;
- replacing a live closing writer;
- a closing writer still accepting;
- stop needing queue space;
- confirmed on any answer;
- partial ambiguous treated as failed;
- unfilled ambiguous treated as failed;
- non-dict records admitted;
- eager thread start;
- replay leaving its writer.

**Mutation proof (round 1): 8/8 caught.**
- synchronous write in the order path;
- blocking put;
- counting before the write (phantom);
- restart ignoring `.1`;
- unknown quantity invented as filled;
- fallback claimed before the decision;
- no writer handover in app.py;
- unfilled-fallback exception not recorded.

## Round 2 dispositions (Codex review ac2afe4 on 2d0e985)

1. **P1: a timed-out handover started a second writer.** Confirmed defect. Fixed by construction:
   - one process-wide `FillWriter` per file path (`fill_writer(path)` registry); a replacement Engine on the same data folder gets the same object, so there is no handover at all, and `app.start_engine` no longer touches telemetry;
   - a writer is replaced only when it is closing AND its thread is provably dead;
   - a stuck writer stays registered as `closing`: new records are dropped and counted, and the state is visible in `telemetry.state`;
   - stopping uses an Event plus join, so it works with a full queue.
   Tests:
   - blocked write + replacement engine: the same writer and the same thread;
   - close times out: bounded, `closing` visible, drop-only, and a new writer only after the old thread ended;
   - close with a full queue still ends the thread.
2. **P2: `fallback=true` meant "call returned".** Confirmed defect. Fixed:
   - `fallback_attempted`, `fallback_confirmed` (only when the fallback order's own record has an executed quantity), `fallback_unconfirmed`, `fallback_pending` (AmbiguousOrder: the lost answer in `_market_entry` and `_add_qty`), `fallback_failed`, `fallback_blocked`, `fallback_skipped`;
   - the child order carries `fallback_order`, and the summary counts only confirmed fallbacks.
   Tests: missing or zero executedQty on unfilled and partial makers gives not confirmed; ambiguous answers on unfilled and partial makers give pending, not failed.
3. **P2: a malformed line broke /api/status.** Confirmed defect. Fixed: `_fill_normalize` admits only dicts with a string `kind`; wrong numeric types and NaN/inf become null; a non-string outcome becomes `unknown`. Rejects are counted as `invalid_records`, and aggregation is defensive per record. Tests: `[]`, scalar, string, null, non-string kind, missing kind, a truncated line, wrong field types, and mixed old/new schema (an old unverified `fallback: true` is not counted).
4. **P2: writer lifetime.** Confirmed defect. Fixed:
   - lazy thread: engines that never emit start none;
   - the thread targets the writer, not the engine;
   - `close_fill_writer(path)` in replay `finally`;
   - an autouse fixture in `tests/conftest.py` closes all writers after every test;
   - atexit closes all writers.
   Tests: five engines start no thread until they emit, and no thread remains after close; construction failure starts no thread; static check on replay.

## Round 1 dispositions (Codex review 3633ce6 on 153cf6d)

1. **P1: telemetry could delay protection after a fill.** Confirmed safety concern. Fixed: order paths only build a record and hand it over with `put_nowait`, and one daemon writer owns rotation and writes. A full or broken queue drops the record (counted). The hung-writer test blocks every write and proves entry → lot persisted → stop, add → stop replacement, and close all complete in under 3 s. The static guard keeps file I/O out of the order-path functions.
2. **P2: phantom statistics.** Confirmed defect. Fixed: the summary is computed only from records the writer has persisted, under a lock. `telemetry.accepted / persisted / dropped / write_errors` make loss visible. A failed write leaves `by_kind` empty, and the restart agrees.
3. **P2: partial maker claimed a fallback that did not run.** Confirmed defect. Fixed: the maker record is built first and completed only after the decision and exchange result. It records exactly one of `fallback` (the order filled), `fallback_blocked`, `fallback_failed` or `fallback_skipped`, and is then emitted. The unfilled path does the same for a failed or raising market fallback; the exception still propagates as before. Trading order and conditions are unchanged.
4. **P2: rotation broke the restart summary.** Confirmed defect. Fixed: the window is the last 5000 persisted records, rebuilt from `.1` + current. Tested with a tiny rotation size and window: identical summary before and after the restart.
5. **P2: missing executedQty reported as a full fill.** Confirmed defect. Fixed: raw `executedQty` is passed. None, '' or 0 gives `qty_fill: null` with `outcome: unknown`. The lot itself still uses the requested quantity as before (no trading change).
6. Also found while fixing P1: an engine restart (settings change) would have left two writers on one file. Fixed with `_fill_stop` in `start_engine`, plus a test.

## Evidence so far (Claude sandbox, stand-in runner, NOT official pytest)

- `tests/test_fills.py`: 15 passed. `tests/test_safety.py` + `tests/test_v31_engine.py` (existing order paths): 123 passed, unchanged.
- Full `tests/` on master + T05: 246 passed + 1 skipped. One failure: `test_summary_has_the_required_provenance_fields`, which needs the real pytest package (environment-only, known).
- `verify.secret_scan`: clean. `fills.jsonl` lives in the data folder (already excluded from the repo and the build).

Round 1 (sandbox, stand-in runner, NOT official pytest), on master d68d6ef (T03b merged) + these files:
- tests/test_fills.py: 28 passed;
- full tests/: 305 passed, 2 skipped, plus the known environment-only provenance failure;
- secret scan: clean.

Round 2 (sandbox, stand-in runner, NOT official pytest), on master d68d6ef + these files:
- tests/test_fills.py: 45 passed;
- full tests/: 322 passed, 2 skipped, plus the known environment-only provenance failure;
- strict replay 1 (24 steps): GATE PASS, 133/133 trades matched, return gap 2.81 pp, and the replay closes its writer.

Pending: GitHub fast/full on the new head (the branch now includes master via a merge commit), and Codex re-review.
