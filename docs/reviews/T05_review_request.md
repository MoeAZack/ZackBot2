# T05: Fill telemetry. Review request

## Current state

| Item | Value |
|---|---|
| Branch | `t05-fill-telemetry`, from protected `master` f9778ca. **Pipeline mode** (owner, 2026-10-06): runs alongside the T03b runtime gate; no shared files with T03b |
| Head for review | the PR head. The full SHA is in the READY FOR CODEX comment |
| Class | **Observe only.** Nothing traded changes: same decisions, orders, sizes, stops and exchange calls |
| Runtime | Nothing installed. The telemetry becomes active with the next approved install |

## The change

Every fill the bot sends itself is recorded as one JSON line in `%LOCALAPPDATA%\ZackBot\fills.jsonl` (rotated to `fills.jsonl.1` at 5 MB):

| Field | Meaning |
|---|---|
| `kind` | `entry_market`, `entry_maker`, `entry_fallback`, `pyramid_add`, `safety_order`, `exit` |
| `expected` / `actual` | The price the bot decided at (the mark used for sizing, the first posted maker price, the exit mark) and Binance's `avgPrice`. A missing `avgPrice` is recorded as `null` and never invented |
| `slip_bps` | (actual - expected) / expected × 10 000, signed so that **+ = worse for us** (buying higher or selling lower) |
| `qty_req` / `qty_fill` / `outcome` | Requested vs filled; `filled`, `partial` or `unfilled` |
| `wait_s` | Seconds from send (maker: from the first posting) to the result |
| extras | `signal_px`, `maker_tries`, `fallback` (only when the market fallback actually ran), `fallback_blocked` (the reason it was refused), `reason` (exit or add reason), `manual` |

- **Summary:** `/api/status` → `health.fills = {by_kind: {n, partial, unfilled, fallback, slip_avg_bps, slip_worst_bps, wait_avg_s}, recent: [last 10]}`. It is rebuilt from the last 2 MB of `fills.jsonl` at start, so it survives restarts.
- **Hooks:**
  - `_market_entry` (market entries and the market fallback after an unfilled maker entry);
  - `_maker_finalize` (maker filled, partial, or unfilled with fallback or blocked fallback);
  - `_add_qty` (pyramid adds, safety orders, the maker remainder fallback);
  - `_market_close` (exits).
- **Safety:** `_fill` catches everything and only logs a warning, so a broken telemetry file can never block or alter a trade (tested). Dry mode records nothing.

Not covered (known limits, proposed for later):
- exchange-side stop fills: Binance fills these, so their expected-vs-actual needs the stop price plus the fill read from reconcile;
- fills resolved from a lost answer (`pending` → reconcile);
- backtest calibration from this data (roadmap "fill telemetry calibrated").

## Tests (`tests/test_fills.py`, 15)

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

**Mutation proof:** each of these breaks at least one test:
- slip sign ignoring the side;
- an invented actual price;
- telemetry exceptions escaping;
- the blocked-fallback flag;
- no restart reload;
- the maker expectation taken from the signal instead of the posted price.

## Evidence so far (Claude sandbox, stand-in runner, NOT official pytest)

- `tests/test_fills.py`: 15 passed. `tests/test_safety.py` + `tests/test_v31_engine.py` (existing order paths): 123 passed, unchanged.
- Full `tests/` on master + T05: 246 passed + 1 skipped. One failure: `test_summary_has_the_required_provenance_fields`, which needs the real pytest package (environment-only, known).
- `verify.secret_scan`: clean. `fills.jsonl` lives in the data folder (already excluded from the repo and the build).

Pending: GitHub fast/full, and Codex review.
