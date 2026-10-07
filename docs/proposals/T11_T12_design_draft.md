# T11 / T12: SQLite trade-event store + decision records and "Why?" view (design draft)

Status: draft proposal for Codex placement. Prepared by Claude on 2026-10-07. Base: master + T05b + T05a + the T06–T09 core stack. Scope: ROADMAP Phase 2, tickets T11 (Feature Off) and T12 (Observe). TESTNET only.

## 0. Constraints from the code

- **History and missed lists are written synchronously.**
  - `_finish()` appends to `history` (capped at 3000) and calls `save_json`.
  - `miss()` and `_add_gate()` append to `missed` (capped at 600).
  - `save_json` writes a temp file then calls `os.replace`, without fsync.
- **`FillWriter` (T05) is the pattern to copy:**
  - admission is one atomic step under the lock (closing check, `put_nowait`, counters);
  - the writer thread starts lazily;
  - there is one process-wide writer per absolute path, shared across settings restarts;
  - `close()` is bounded and registered with `atexit`.
- **T05a audit events** are already cleaned (`TA.clean`) and versioned.
- **Reason codes:** `core/reasons.info(text)` returns stage, code and detail. `Decision.refuse(text)` keeps the exact text.
- **`core/` stays pure**, so the store and the renderer live at the top level.
- **Golden must not move:** the golden test hashes history, missed and the curves. This design adds nothing to lots or `state.json`.

## 1. T11: SQLite trade-event store (Feature Off)

### 1.1 Files

- **`event_store.py` (new):**
  - schema and migrations;
  - `EventWriter` and the `event_writer(path)` registry;
  - `Reader`, `eid_for()`, `backfill()`, `parity()`, `verify_chain()`.

  It never imports engine.
- **`engine.py`:**
  - `F['events']` pointing at `trade_events.db`;
  - an `_ev(kind, **f)` helper that never raises and returns immediately when the store is off;
  - the hooks in §1.6;
  - `event_store_summary()`.
- **`app.py`:** `GET /api/events` and validation of the `EVENT_STORE` setting.
- **`verify.py`:** a parity line when shadow mode is on during a replay.

### 1.2 Schema (v1)

```sql
PRAGMA user_version = 1;
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE events(
  seq INTEGER PRIMARY KEY,           -- insertion order; order of the hash chain
  eid TEXT NOT NULL UNIQUE,          -- deterministic idempotency key
  v INTEGER NOT NULL, ts TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  account TEXT NOT NULL,             -- 'default' until AccountContext ids (T15)
  venue TEXT NOT NULL,               -- testnet | mainnet | replay | backtest(reserved)
  source TEXT NOT NULL,              -- engine | grid | backfill:* | store
  symbol TEXT, side TEXT, sleeve TEXT, lot_key TEXT, cand_key TEXT, dec_id TEXT,
  kind TEXT NOT NULL, stage TEXT, code TEXT,
  text TEXT,                         -- exact user-facing text
  payload TEXT NOT NULL,             -- canonical JSON (sort_keys)
  prev_hash TEXT, hash TEXT NOT NULL -- sha256 chain (Phase 2 integrity)
);
-- indexes on lot_key, cand_key, dec_id, (account,symbol,ts_ms), (kind,ts_ms)
-- triggers: UPDATE always aborts; DELETE aborts except old 'audit_bulk' rows past the retention cut
```

**Event kinds**
- `missed`: an exact mirror of the missed.json record.
- `trade_closed`: an exact mirror of the history.json record.
- `lot_open`, `fill`, `exec`.
- `audit` and `audit_bulk`: T05a events. `audit_bulk` covers excursion checkpoints and `hold_eval`.
- `decision`: added by T12.
- `store`: lifecycle and gap markers.

**`eid`** is built from each record's natural key, so live writes, backfill, catch-up and parity are all idempotent. It is hashed with sha256 and truncated to 32 hex characters.

**Mirror rule:** the payload is byte-identical to the JSON record. Scrubbing happens only on export.

### 1.3 Writer (copy of the FillWriter pattern; FillWriter itself unchanged)

**Queue and drops**
- Queue size `EVENT_QUEUE_MAX = 2000`. When full, the newest event is dropped.
- Above 80 % full, `audit_bulk` and `exec` events are shed first.
- Counters: accepted, persisted, dropped, `dropped_by_kind`, write_errors, lost_on_error, batches, last_commit_t, last_error, queued and state.
- A gap in any mirror kind sets `gap=True`. The next start repairs it from JSON.

**Writer thread**
- The thread owns the SQLite connection.
- It batches up to 256 events or 250 ms per transaction: `BEGIN IMMEDIATE`, then `INSERT OR IGNORE` per event, then `COMMIT`.

**Errors**
- A failed batch rolls back and backs off up to 30 s. The exponent is capped first, as in the T05b retry fix.
- After 3 consecutive failures the state becomes `degraded`. The engine raises the `event-store` incident through `err(key=)`.
- The writer never notifies anyone itself.

**Pragmas:** WAL, `synchronous=NORMAL`, `busy_timeout=2000`, `wal_autocheckpoint=1000`, `temp_store=MEMORY`, `auto_vacuum=INCREMENTAL`.

**Shutdown:** `close()` stops admission, flushes within a bound, runs `wal_checkpoint(TRUNCATE)`, closes the connection and joins the thread (also from `atexit`).

### 1.4 Feature flag

- **Setting:** `EVENT_STORE` = `off` (default), `shadow`, or `primary` (reserved and refused until Phase 2). It can be overridden with the env var `ZB_EVENT_STORE`.
- **off:** no writer, no file, `_ev` returns on its first line.
- **shadow:** JSON stays authoritative for every reader; SQLite receives writes only.

### 1.5 Backfill and catch-up

**Backfill**
- Runs on the first shadow start, as the writer thread's first job.
- Sources: history.json, missed.json, trade_audit.jsonl and its rotation, fills.jsonl and its rotation.
- Works in chunks of 500 with a resumable cursor per source. Malformed lines are counted, never fatal.

**Every start:** the last 600 missed and last 3000 history records are offered again with `INSERT OR IGNORE`. This repairs drops and losses.

### 1.6 Engine hooks (emit only)

Hooks sit in `miss()`, `_add_gate()`, `_finish()`, `_create_lot()`, `_apply_add()`/`_apply_close()`, `_fill_emit()` and `_audit_emit()`.

Each hook is wrapped in try/except. None adds a `save_state` call or takes an extra lock.

### 1.7 Retention

- Mirror kinds, decisions, `lot_open` and `fill` are kept forever.
- `audit_bulk` is pruned after 90 days. A chain-anchor `store` event is written first, then `incremental_vacuum` runs.
- A size alarm fires at 512 MB.

### 1.8 Crash safety

- Each batch is one WAL transaction, so a kill loses at most the uncommitted batch, never part of a row.
- On open:
  1. run `quick_check` (bounded);
  2. if it fails, rotate the file to `.corrupt-<utc>` (with its `-wal` and `-shm`), recreate it, backfill and raise an incident;
  3. run `verify_chain` on the last 1000 rows; a break is reported, never repaired.

### 1.9 Windows

- The store lives in `%LOCALAPPDATA%\ZackBot`. UNC paths are refused.
- Only one writer per process exists.
- Rotation happens only after `close()` and the thread join.
- Readers open short-lived read-only connections.
- The installer and rollback drill never touch `trade_events.db*`; a test asserts this.
- The exe self-test asserts `sqlite3.sqlite_version >= 3.35`.

### 1.10 Read API and parity

- **Reader methods:** `for_lot`, `for_cand`, `decision`, `recent(limit ≤ 500)`, `stats` and `parity`.
- **`GET /api/events`** never takes `engine.lock`.
- **Parity check:** JSON vs DB inside the JSON window gives `json_only`, `db_only` (ignored) and `mismatched`.
  - It runs at start after catch-up, on demand, and in replay.
  - Acceptance: `json_only == mismatched == 0`.

## 2. T12: decision records + "Why?" view (Observe)

### 2.1 Record

- **`core/trace.py` (pure):**
  - `Trace` holds `dec_id`, `kind` (entry, skip, warning, add, add_blocked, close or partial_close), `cand_key`, `lot_key` and `steps`;
  - `Step` holds `stage`, `name`, `ok`, `reason`, exact `text` and `numbers`;
  - `GATE_ORDER` gives the gate order.
- **Collection in the engine:** in memory only, along the existing path:
  - **signal:** sides before side-masking;
  - **cycle gates and `entry_block`:** the first refusal (exact text) or pass, plus warnings;
  - **sizing:** sleeve equity, risk %, kelly, governor, risk USD, qty, stop and R;
  - **AI filter;**
  - **leverage outcome;**
  - **order:** market or maker, expected price, fill-telemetry link;
  - **outcome;**
  - **close:** `exit_reason`, trigger numbers, pnl/R, T05a audit link;
  - **adds and blocked adds.**
- **Emission:** each trace is emitted once, as a `decision` event at its terminal point. With the store off, nothing is collected.
- **No new gate is evaluated.** Gates that were not reached show as "not checked".

### 2.2 Why renderer

`why.py` returns a view with sections and a coverage of full, legacy or json_only. `render_text()` produces the shared panel and Telegram text.

When no decision exists, it falls back to the miss reason with stage/code, or `exit_reason` + fills + the T05a summary.

### 2.3 Panel (additive)

- A "Why?" button is added to the closed, open and missed tables.
- It opens a dialog through `esc()` with a Copy button.
- The data comes from `GET /api/why?lot=|miss=`, fetched only on click.
- Existing columns and texts are unchanged.

### 2.4 Telegram

- New commands: `/why <COIN>` and `/why missed <COIN>`, truncated to 3500 characters.
- Existing notifications and `/help` stay byte-identical. Adding `/why` to `/help` is Q6.

### 2.5 Tests

- Schema, pragmas and triggers.
- Kill mid-write: 20 random `TerminateProcess` runs, then reopen. The file passes `quick_check`, `seq` is contiguous, and the chain is valid.
- Queue overflow: `emit` takes under 1 ms, low-value kinds are shed first, and catch-up repairs the gap.
- The admission/close race (ported T05 test).
- A locked or read-only DB degrades with one incident, and trading continues.
- Windows reader/writer and rotation; UNC paths refused; the installer leaves the DB alone.
- Parity is 0 for every golden scenario in shadow mode.
- Golden is unchanged in both off and shadow modes, without re-recording.
- No I/O happens on the loop thread.
- T12 completeness: every `miss(`, `_add_gate` and exit `why` literal emits a decision whose reason is not OTHER.
- `core/trace.py` stays pure.
- Why rendering is tested for all three coverage levels.
- The UI baseline is unchanged except for the new checks.

### 2.6 PR split

1. **T11-1:** the store module with unit, crash, overflow and Windows tests.
2. **T11-2:** engine hooks behind `off`, settings, health/incident, catch-up, API, parity in verify, golden in shadow mode. Then a 48 h testnet shadow canary.
3. **T12-1:** trace builder, decision events, completeness and purity tests.
4. **T12-2:** `why.py`, `/api/why`, panel button and dialog, Telegram `/why`, UI baseline additions.

### 2.7 Risks

- The PyInstaller build must include `_sqlite3`. The exe self-test asserts the version.
- JSON encoding on the emitting thread costs some GIL time. Keep traces under 2 KB.
- Disk growth from T05a checkpoints. Covered by retention and the size alarm.
- The hash chain follows insertion order, not timestamps.
- Exports must scrub, because mirror payloads keep the exact miss texts.
- Two engines during a settings restart must share one writer. Covered by the registry and a test.
- The JSON caps are smaller than the store, so the UI must not silently mix the two.

### 2.8 Open questions for Codex

- **Q1:** Should mirror payloads stay byte-exact, or be cleaned?
- **Q2:** Should `synchronous` be NORMAL or FULL from day one?
- **Q3:** Is drop-newest plus a watermark enough, or are per-kind queues needed?
- **Q4:** Should the hash chain ship in T11 or be deferred?
- **Q5:** Should trades.csv be backfilled?
- **Q6:** Should `/why` be added to `/help`?
- **Q7:** One DB per account now, or an account column until T15?
- **Q8:** Should T12 traces also be built in the backtest now?
- **Q9:** Should `FillWriter` and `EventWriter` later be extracted into a shared `BoundedWriter`, as a separate no-behaviour-change ticket?
