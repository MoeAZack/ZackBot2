# T05: Fill telemetry. Review request

## Current state

| Item | Value |
|---|---|
| Branch | `t05-fill-telemetry`, from protected `master` f9778ca. **Pipeline mode** (owner, 2026-10-06): runs alongside the T03b runtime gate; no shared files with T03b |
| Head for review | the PR head. The full SHA is in the FIXED FOR CODEX comment (round 1 reviewed 153cf6d; Codex review 3633ce6) |
| Class | **Observe only.** Nothing traded changes: same decisions, orders, sizes, stops and exchange calls |
| Runtime | Nothing installed. The telemetry becomes active with the next approved install |

## The change

Every fill the bot sends itself is recorded as one JSON line in `%LOCALAPPDATA%\ZackBot\fills.jsonl` (rotated to `fills.jsonl.1` at 5 MB). Since round 1, order paths only build the record and `put_nowait` it into a bounded queue (1000). A single background writer does every file operation (see "Round 1 dispositions"):

| Field | Meaning |
|---|---|
| `kind` | `entry_market`, `entry_maker`, `entry_fallback`, `pyramid_add`, `safety_order`, `exit` |
| `expected` / `actual` | The price the bot decided at (the mark used for sizing, the first posted maker price, the exit mark) and Binance's `avgPrice`. A missing `avgPrice` is recorded as `null` and never invented |
| `slip_bps` | (actual - expected) / expected × 10 000, signed so that **+ = worse for us** (buying higher or selling lower) |
| `qty_req` / `qty_fill` / `outcome` | Requested vs filled; `filled`, `partial`, `unfilled`, or `unknown` (Binance gave no executedQty: `qty_fill` is `null`, never the requested quantity) |
| `wait_s` | Seconds from send (maker: from the first posting) to the result |
| extras | `signal_px`, `maker_tries`, `reason` (exit or add reason), `manual`. Maker records state what actually happened to the fallback: `fallback` (true only when the fallback order filled), `fallback_blocked` (the reason it was refused), `fallback_failed` (the order error), `fallback_skipped` (no lot, or remainder below the minimum) |

- **Summary:** `/api/status` → `health.fills = {by_kind: {n, partial, unfilled, unknown, fallback, slip_avg_bps, slip_worst_bps, wait_avg_s}, recent: [last 10], telemetry: {accepted, persisted, dropped, write_errors, queued, window, in_window}}`.
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
  - On a settings restart, `start_engine` (under the old engine's lock, outside order handling) lets the old writer finish within 2 s, then ends it, so two writers never share the file. At exit, a 2 s flush runs via atexit, holding only a weak reference to the engine.

Not covered (known limits, proposed for later):
- exchange-side stop fills: Binance fills these, so their expected-vs-actual needs the stop price plus the fill read from reconcile;
- fills resolved from a lost answer (`pending` → reconcile);
- backtest calibration from this data (roadmap "fill telemetry calibrated").

## Tests (`tests/test_fills.py`: 15 from round 1 + 13 new = 28 in the runner; the parametrized test counts once)

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

**Mutation proof (round 1): 8/8 caught.**
- synchronous write in the order path;
- blocking put;
- counting before the write (phantom);
- restart ignoring `.1`;
- unknown quantity invented as filled;
- fallback claimed before the decision;
- no writer handover in app.py;
- unfilled-fallback exception not recorded.

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

Pending: GitHub fast/full on the new head, and Codex re-review.
