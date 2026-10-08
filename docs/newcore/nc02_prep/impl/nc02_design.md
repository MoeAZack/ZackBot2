# NC-02 state/event store - concrete design (PRE-STAGE, Claude Code)

Status: design draft for Codex (Integration), 2026-10-08 (Cairo). Prepared on `prep/nc02-negative-fixtures` at `85f1e24`.
It implements nothing. Inputs: `nc02_acceptance_draft.md` r4 (A01..A24, M01..M53, I01..I04), fixtures NF-01..49,
`docs/newcore/nc01_prep/*`, and the NC-01 contract (PR #36, `docs/newcore/NC01_CONTRACT.md`). Plan row C2: "Account-keyed
snapshots plus append-only intent/result events; atomic migration/recovery. Proof: crash matrix at every write
boundary; deterministic replay; no secrets."

Binding Codex rulings honoured throughout: intent durable before send; result durable before apply; unknown ownership is
`None`; known-empty only on a proven first run with a flat exchange; no legacy import; integer UTC ms; evidence copied,
never moved, kept in an encrypted access-restricted envelope with only hash + metadata exposed; future/unknown format
aborts with zero writes; generation counters detect rollback; a migration writes a new generation; hard HOLD allows only
the A23/A24 emergency set; exchange down allows local journaling but no promotion.

Every place with options ends in **Recommend:**. Items that need Codex's call are collected in section 12.
Statements about Windows file-system behaviour are marked **(inferred)** where they are not yet measured on the target
machine; the crash harness (section 10) is how they get measured.

---

## 1. Package and seams

- Package `newcore/store/` (NC-01 lives in `newcore/domain/`). It imports `newcore.domain` and the stdlib only, plus one
  optional platform shim for the evidence cipher (section 8).
- All file I/O goes through one injected **`FsSeam`** object. No `open()`/`os.*` call in `newcore/store` outside the
  real seam implementation (an AST test enforces it, the same way NC-01 enforces its import boundary). That seam is
  where the fixture runner and the crash harness inject ENOSPC, EROFS, PermissionError, tears and crashes.
- Time comes from the injected AUD-12a clock as integer UTC ms. The store never reads the wall clock itself.
- The exchange is never called by the store. Recovery receives an `ExchangeView` (snapshot + order lookups) from NC-03
  through the NC-04 account worker; the store only classifies and journals.

```python
class FsSeam(Protocol):            # every path is absolute; every method may raise OSError
    def lstat(self, p) -> os.stat_result: ...          # never follows reparse points
    def listdir(self, p) -> list[str]: ...
    def read_bytes(self, p) -> bytes: ...
    def open_new(self, p) -> Handle: ...               # O_CREAT|O_EXCL|binary; never truncates an existing file
    def open_append(self, p) -> Handle: ...
    def open_slot(self, p) -> Handle: ...              # existing fixed slot file, read/write, no truncate on open
    def write(self, h, data: bytes, at: int | None = None) -> None: ...
    def truncate(self, h, n: int) -> None: ...         # only used on HEAD/anchor slots
    def fsync(self, h) -> None: ...                    # FlushFileBuffers on Windows
    def fsync_dir(self, p) -> None: ...                # see 5.4
    def close(self, h) -> None: ...
    def mkdir(self, p) -> None: ...
    def set_private_acl(self, p) -> None: ...          # section 8.3
    def unlink_own_partial(self, p) -> None: ...       # ONLY an unindexed evidence blob of this attempt (D4)
```

There is deliberately no `rename`/`replace` in the seam: the design never needs one (section 4.1), which removes the
Windows sharing-violation-on-rename class (antivirus/indexer handles) and the "is MoveFileEx atomic here" question.

## 2. Directory layout

```
<base>\                                       Windows: %LOCALAPPDATA%\ZackBot\NewCore   (POSIX: $XDG_STATE_HOME/zackbot/newcore)
  data\                                       the "data folder" of A01 (its tree is what ABORT-RO hashes)
    accounts\
      acct_<uuid4>\                           one per AccountId (NC-01 D3.1: opaque, never derived from the key)
        HEAD.a  HEAD.b                        dual-slot commit record (4.1); the ONLY commit point
        snap\
          g00000000000000000007.snap          snapshot of generation 7 (20-digit zero-padded, sortable)
          g00000000000000000006.snap          retained previous generation (candidate source, A07)
        log\
          g00000000000000000007-000001.seg    append-only event segment(s) of generation 7
          g00000000000000000006-000001.seg    retained with its snapshot
        evidence\                             private DACL (8.3); NEVER a member path
          index.seg                           append-only evidence index (metadata only, framed like a segment)
          blobs\<sha256>.<incident>.zbe       encrypted envelopes (8)
  anchors\                                    OUTSIDE data\  (decision D2)
    <account_id>.a  <account_id>.b            dual-slot high-water anchor + hard-HOLD marker
    by-binding\<binding_digest>               one line: the account_id bound to that digest
  incidents\
    boot.jsonl                                out-of-band boot/abort incident log (ABORT-RO, hard HOLD); per install (open Q2)
  run\
    <account_id>.lock                         single-instance lock (LockFileEx / flock); outside data\ so ABORT-RO writes nothing in data\
```

- Legacy files (`state.json`, `settings.json`, `install.json`, `*.bak`, `*.corrupt-*`, `state.json.*.tmp`) can only
  appear in `data\` itself (or the legacy `%LOCALAPPDATA%\ZackBot` root). They are never under `accounts\`, so rule 0
  (REJECT) is a pure name scan; the store never opens them except read-only to hash them for the report.
- Several accounts can share `data\` (NC-04 two-account proof). Account selection at boot: the configured binding's
  non-secret `binding_digest` is looked up in `anchors\by-binding\`, then confirmed against `HEAD` (read-only). Two
  account dirs claiming the same binding is HOLD (A11).
- **Settings are not a separate member.** Settings changes are `SettingsChanged` events; compaction embeds the settings
  record in the snapshot. So the legacy "settings member F>R / damaged" fixtures (NF-03/04/12) map to a settings record
  inside a generation (decision D13).

**Recommend:** this layout; `<base>` refuses an OneDrive/Dropbox-synced path at start (sync clients rewrite and lock
files) with an incident, and refuses a network path (no reliable flush semantics).

## 3. On-disk formats

### 3.1 File frame (all store files)

```
FileHeader  8 bytes : b"ZBNC" | file_kind:u8 | frame_version:u8 | reserved:u16 = 0
                      file_kind: 1 snap, 2 seg, 3 head slot, 4 anchor slot, 5 evidence index, 6 evidence envelope
Record     12 bytes : sync:u16 = 0xB25A | rtype:u8 | flags:u8 | length:u32 LE | crc32:u32 LE   then `length` payload bytes
                      crc32 = zlib.crc32(rtype|flags|length|payload)   (stdlib; CRC-32C would need a dependency)
```

- `frame_version` unknown to the reader -> the file's format is **F?** -> rule 1 (ABORT-RO). Bad magic at a member path is
  **damage** (rule 4): an all-NUL file (NF-28) or a valid document followed by NULs (NF-27) is damage, never "unknown".
- `length` is bounded (record <= 16 MiB, snapshot <= 64 MiB). A length past EOF is a torn record (3.4), a length above the
  bound with a valid CRC cannot occur from this writer and is damage.
- The payload of every record is **canonical UTF-8 JSON** from the NC-01 codec (sorted keys, no whitespace, Decimal as
  strings, integers only for counters and ms). Parsing is strict (A17): `object_pairs_hook` rejects duplicate keys,
  `parse_constant` rejects NaN/Infinity, `parse_float` rejects every float, `parse_int` rejects > 19 digits or outside
  int64, and nothing may follow the document. A failure is a typed `InvalidRecord(code, byte_offset)`; the message never
  quotes content (A16).

### 3.2 Header record (record 0 of every file, rtype 1)

```json
{"account_key":"acct_...","file_kind":"snap","generation":7,"writer_build":"nc-412+1a24e70","writer_seq":412,
 "format_version":1,"min_reader_version":1,"written_ms":1791400060000,"lsn_base":1834,"prev_file_sha256":"..."}
```

- `generation`, `format_version`, `min_reader_version`, `writer_seq`, `written_ms`, `lsn_base` must be JSON integers >= 0
  and not booleans. Any other type is an **unknown** format (R-VERSION-TYPE -> rule 1), checked before anything else in
  the file is decoded. `min_reader_version > R` is **F>R** (rule 1).
- `writer_build` is display text; ordering uses the integer `writer_seq` (decision D8: the acceptance draft compares
  "newer build" but a git SHA has no order).
- The reader peeks only the file header and record 0 of every file in the account dir (members **and** orphans) before
  it decodes any body, so rule 1 is decided with the minimum of parsing and before any write.

### 3.3 Snapshot file (`snap\gNNN.snap`)

Records in order: header(1) - binding(2) - settings(3) - portfolio(4) - provenance(5) - end(255).

- **binding**: NC-01 `AccountBinding` (venue, environment, base asset, non-secret key digest, confirmed flag) + `account_id`.
  Must equal `HEAD.binding_digest`; a mismatch is A11 damage.
- **portfolio**: NC-01 `Portfolio` with explicit `ownership`: `known` with collections, or `unknown` with collections
  `None` (never `[]`). Lots, intents (one `OrderIntent` family), protections, open `OrderResult`s.
- **provenance**: how this generation came to be: `init_flat{recon_lsn}` | `compaction{from_generation, lsn_upto}` |
  `migration{from_generation, from_format, from_sha256}` | `promotion{recon_lsn}` | `hold{incident_ids}`, plus
  `trust: MANAGED | HOLD | HOLD_INIT` and, when the portfolio is empty and `ownership=known`, the `known_empty_proof`
  (`init_flat` or a `result_chain` from the last non-empty generation, listing the result lsns). An empty portfolio
  without a proof decodes as `ownership=unknown` (R-KNOWN-EMPTY, NF-43).
- **end** (rtype 255): `{"sha256": <of every preceding byte>, "records": 6}`. Nothing may follow it (A17: trailing bytes,
  NUL padding included, are damage).
- Snapshot files are written once (`open_new`, O_EXCL) and never modified. They are members only while `HEAD` names them
  (current or retained); any other `.snap` is an orphan (R-UNCOMMITTED).

### 3.4 Event segment (`log\gNNN-SSSSSS.seg`)

- Header record (rtype 1) with `generation`, `segment_no`, `lsn_base`.
- Then event records (rtype 10..). Each payload carries `lsn` (account-wide monotonic integer), `prev` (first 16 hex of
  sha256 of the previous record's payload; detects splices/reordering) and `t_ms`.
- Event types (all NC-01 records): `IntentRecorded`, `IntentSubmitted` (optional ack), `ResultObserved` (`OrderResult` +
  `ResultPhase` + `Evidence`), `HoldEntered{cause, scope, incident_id}`, `HoldItemResolved`, `IncidentRecorded`,
  `EvidenceLinked{incident_id, sha256}`, `ReconciliationRecorded{exchange_snapshot (sanitized), taken_ms, verdict,
  items}`, `OwnerAction{item, action, confirmation_id}`, `BindingConfirmed`, `SettingsChanged`, `SegmentSealed`.
- **Torn tail** (A15): scanning from the header, the tail starts at the first offset where a record is incomplete
  (`offset + 12 + length > EOF`), the remaining bytes are all NUL (NTFS zero-fill after a crash **(inferred)**), or the
  CRC fails **and no later offset holds a record with a valid sync + CRC**. If a valid record exists after a bad one, it is
  **mid-segment damage** (rule 4, M25), never a tail.
- Repair **Recommend (decision D3): seal-and-roll, no truncate.** The tail bytes are first copied into the evidence
  envelope (A04 steps), then the next `HEAD` commit records the segment as `sealed_len = <last good offset>` and opens a
  new segment. The torn bytes stay in place (copy-never-move holds even for the tail), readers stop at `sealed_len`, and
  there is no truncate whose durability needs its own reasoning. A15 says "truncated"; seal-and-roll meets its intent
  ("repair never destroys bytes") more strictly. If Codex prefers truncate, the boundary list in 10.3 already has it.

### 3.5 HEAD slots (`HEAD.a`, `HEAD.b`)

One small framed file each: header + head record (rtype 20) + end. Head record:

```json
{"account_key":"acct_...","commit_seq":58,"binding_digest":"...","generation":7,
 "snapshot":{"name":"g...07.snap","sha256":"...","len":2311},
 "segments":[{"name":"g...07-000001.seg","sealed_len":40960},{"name":"g...07-000002.seg","sealed_len":null}],
 "retained":[{"generation":6,"snapshot":{...},"segments":[...]}],
 "high_water":{"generation":7,"writer_seq":412,"lsn_floor":1834},
 "writer_history":[{"writer_seq":410,"from_commit":1},{"writer_seq":412,"from_commit":51}],
 "open_incidents":["inc_..."],"format_version":1,"min_reader_version":1,"written_ms":1791400060000}
```

- The reader takes the valid slot (magic, CRCs, end sha256) with the highest `commit_seq`. Two valid slots with the same
  `commit_seq` but different content is damage. One torn slot next to a valid one is the expected crash-during-commit
  state, not damage. Both invalid with any snapshot present is damage (rule 4); both absent with only an `init` G1
  snapshot is an interrupted INIT (7.2).
- The writer always overwrites the slot that does **not** hold the current head (the older or invalid one), in place:
  `open_slot`, write at 0, truncate to length, fsync. One fsync commits.
- `HEAD` holds pointers and the high-water only. Trust (MANAGED/HOLD) is **not** in `HEAD`: it is derived from the
  snapshot provenance plus the events after it, so there is one source of truth (R-SINGLE-SOURCE).

### 3.6 Anchor slots (`anchors\<account_id>.a/.b`) - generation high-water outside the data folder

Same dual-slot scheme: `{account_key, binding_digest, commit_seq, generation, writer_seq, written_ms, hard_hold?}`.

- Why: a whole-folder rollback (restore an old copy of `data\`, NF-48 / M42) rolls `HEAD` back too. The anchor lives
  outside `data\`, so it usually survives, and `anchor.generation > HEAD.generation` is rule 2 directly. When it does not
  survive, the exchange difference still yields HOLD (R-ROLLBACK-DETECT); the anchor is a second line, not the only one.
- Written strictly **after** the `HEAD` commit, so a crash can leave `anchor < HEAD` (harmless: the reader uses
  `max(HEAD.high_water, anchor)` and the next commit refreshes it) but never `anchor > HEAD` except through a rollback.
- `hard_hold` (decision D11): best-effort marker written when the store becomes unwritable (it is often still writable
  outside `data\`, e.g. a read-only data volume). On restart, a hard-HOLD marker newer than the head forces HOLD even if
  the store is writable again (M53 "the normal HOLD path applies").
- `by-binding\<digest>` lets an emptied `data\` (NF-16 analogue) with a surviving anchor be recognised as "this binding
  had generation G": rule 2 HOLD, not a fresh INIT (decision D2).

## 4. Write protocols (exact order, an fsync at each step)

Notation: `W` write, `F` fsync of that file, `D` fsync of the directory, `|commit|` the durability point. Every step is a
crash boundary (section 10 numbers them). A failed `W`/`F`/`D` poisons the store for the rest of the process (never
retry the same handle after a failed fsync: its dirty pages may already be dropped **(inferred from the Linux fsyncgate
behaviour; Windows not measured)**) and moves the account to hard HOLD (6.3).

### 4.1 Commit a new generation (INIT, compaction, migration, HOLD snapshot, promotion)

```
S0  build the next state in memory; encode with the NC-01 codec; DECODE it again with the reader and compare canonical
    bytes (A18 "the writer validates like the reader"); a mismatch refuses the commit (nothing written)
S1  open_new  snap\g<G'>.snap        G' = max(HEAD.generation, highest generation seen in ANY file name or header) + 1
S2  W all records ; F
S3  D snap\
S4  open_new  log\g<G'>-000001.seg ; W header record ; F
S5  D log\
S6  open_slot HEAD.<older> ; W head{commit_seq+1, generation G', ...} at 0 ; truncate ; F        |commit|
S7  open_slot anchors\<id>.<older> ; W ; F                                                       (best-effort if D2 off)
S8  later, separately: retention GC (never a generation that is current, the newest retained, referenced by an open
    incident, or named by a reconciliation record that has not been superseded)
```

- Generations are monotonic, not contiguous: a crash between S1 and S6 leaves an orphan `g<G'>.snap`; the next commit
  uses `G'+1` because `open_new` never reuses a name and G' is computed from every name seen. The orphan is reported and
  kept (R-UNCOMMITTED).
- There is no tmp file and no rename: the snapshot is uncommitted until `HEAD` names it.

### 4.2 Append an event (intent before send, result before apply)

```
E0  validate the record with the reader schema (A18; R-INTENT-SHAPE: kind, client id, created_ms int, qty > 0, whole
    steps, <= venue max); refuse -> nothing written, nothing sent, reason code returned
E1  W frame to the active segment (open_append)
E2  F segment                                                                                     |commit|
E3  only now: return "durable" -> the caller may SEND (intent) or APPLY in memory (result)
```

- `IntentRecorded` precedes the exchange call; the client id sent is the deterministic id stored in the intent (8.5 of
  the NC-01 contract hand-off: NC-03 owns the string format, NC-02 persists it).
- `ResultObserved` precedes any ownership change; replay applies it the same way (deterministic fold).
- Group commit: several events may share one `F` (batching for throughput), but no caller is released before the `F`
  that covers its record returns.
- Segment roll: at 8 MiB, or on compaction. Roll = commit a `HEAD` whose segment list seals the old segment at its
  current length and names a new one (S4, S5, S6 without a snapshot).

### 4.3 Copy evidence (A04, A15, A16)

```
V0  read the original read-only (share READ|WRITE|DELETE); sha256, size, mtime_ns -> integer ms; the original is never
    opened for write, renamed or moved
V1  envelope = seal(plaintext)  (section 8)
V2  open_new evidence\blobs\<sha256>.<incident>.zbe ; W ; F ; D blobs\     (uncommitted until V4 indexes it)
V3  verify: read the blob back, unseal, sha256 equals V0; else unlink_own_partial(blob), report, HOLD stays
V4  W EvidenceCopied{incident, sha256, size, rel_path, mtime_ms, blob, cipher} to evidence\index.seg ; F    |commit|
V5  W EvidenceLinked{incident, sha256} to the account segment ; F   (skipped if the account log is the damaged member;
    the HOLD snapshot's provenance then carries the incident id)
```

- Idempotent: the blob name is keyed by content hash and incident; re-running after a crash finds the indexed entry and
  skips V1-V4. An un-indexed `.zbe` from a crash between V2 and V4 is re-verified (V3) and then indexed, never trusted
  unverified; if it fails verification it is the store's own partial (D4).
- ENOSPC/EROFS at V2-V4: the store deletes only its **own** unindexed blob of this attempt (A04 "no partial evidence is
  left"), the original stays untouched, the account stays HOLD (hard HOLD if the journal is also unwritable), and the
  failure goes to `incidents\boot.jsonl` (out-of-band). This deletion is the single exception to R-UNCOMMITTED and needs
  Codex's call (D4).

### 4.4 Reconciliation and promotion (A08)

```
R1  ExchangeView.snapshot() -> positions, open orders, protective orders, ids, lifecycle, taken_ms (integer UTC ms)
R2  compute the verdict under R-MATCH (binding confirmed and equal; age < MATCH_MAX_AGE_MS; per symbol/side aggregate
    qty within step/2; every owned order/stop id present with the expected lifecycle; no unowned position or order)
R3  append ReconciliationRecorded{generation, lsn_upto, snapshot_hash, sanitized snapshot, verdict, items} ; F  |commit of the claim|
R4  promotion only if verdict in {match, owner-resolved}, the candidate is not trivially empty (or an OwnerAction
    adopted "empty"), and no HoldItem is open: take a FRESH snapshot, re-check R2 against it inside the account worker
    (single writer, sends blocked while HOLD, so "atomic" is a same-turn compare); any change -> stay HOLD
R5  commit generation G' (4.1) with provenance promotion{recon_lsn}, trust MANAGED                        |commit|
```

A crash after R3 and before R5 restarts in HOLD with a reconciliation record that is now stale (older than
`MATCH_MAX_AGE_MS`), so R1-R5 run again. Nothing is half-promoted because MANAGED exists only in a committed snapshot.

### 4.5 Migration (A13)

F<R: decode generation G with the retained F-codec, translate, validate with the R reader (S0), commit G' (4.1) with
provenance `migration{from_generation G, from_format F, from_sha256}` and **trust HOLD** (M11/M12: migrate, then HOLD,
then A08). G and its segments stay in `retained` and are never GC'd until (a) the installer confirms R and (b) a
reconciliation record exists for G'. A down-migration is an owner tool that writes yet another new generation.

### 4.6 INIT (rule 8, A09)

```
I0  preconditions: no account dir and no anchor for this binding digest; ExchangeView.snapshot() succeeded
I1  mkdir accounts\acct_<uuid>\ , snap\ , log\ , evidence\ (set_private_acl on evidence\ BEFORE anything is put in it), blobs\ ; D parent
I2  commit G=1 via 4.1 with provenance init_flat{} (flat exchange: trust MANAGED, ownership known, known_empty_proof
    init_flat) or trust HOLD_INIT with ownership unknown and one HoldItem per exchange position/order (non-flat)
I3  anchors\by-binding\<digest> (W, F) after the HEAD commit
```

Exchange down at I0: INIT waits; nothing is written anywhere (M23, A12).

## 5. Platform notes (Windows first, portable)

5.1 `F` = `os.fsync` = `FlushFileBuffers`. On POSIX `os.fsync`; on macOS use `fcntl(F_FULLFSYNC)`.
5.2 Files are opened with Python's default share mode plus `FILE_SHARE_DELETE` for read-only probes (a small ctypes
    `CreateFileW` in the real seam), so a reader never blocks the owner's tools.
5.3 Reparse points and non-files: `lstat` + `st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT`, or not `S_ISREG`, at a
    member path = rule 3 (HOLD, never followed). Covers open question 5 (symlinks/junctions) without needing the
    privilege to create one in tests (the seam can report a fake reparse attribute).
5.4 `D` on Windows: `CreateFileW(dir, GENERIC_WRITE, ..., FILE_FLAG_BACKUP_SEMANTICS)` + `FlushFileBuffers`. On NTFS
    the directory entry is journaled metadata and is usually durable once the file's own flush returns **(inferred;
    must be measured with the real-kill drill, 10.5)**; if the call is refused (`ERROR_ACCESS_DENIED`/`INVALID_FUNCTION`)
    the seam records a capability flag and continues. POSIX: `os.open(dir, O_RDONLY)` + `os.fsync`.
5.5 Error classification by `winerror`: 112 `ERROR_DISK_FULL` and 39 `ERROR_HANDLE_DISK_FULL` -> ENOSPC; 19
    `ERROR_WRITE_PROTECT` -> EROFS; 5 `ERROR_ACCESS_DENIED` -> permission; 32/33 sharing/lock violation -> **transient**:
    retried with backoff for at most 2 s (antivirus/indexer) before it becomes rule 3. Every other OSError is a fault.
5.6 Names are lowercase ASCII with fixed width (case-insensitive file systems, sortable listings). Paths stay well under
    260 characters from `%LOCALAPPDATA%`; the seam uses `\\?\` if a configured base is long.
5.7 Locks: `run\<id>.lock` with `msvcrt.locking`/`LockFileEx` (POSIX `fcntl.flock`), taken before the first read, outside
    `data\` so taking it is not a store write. A held lock refuses start (another instance), it is not HOLD.

## 6. Recovery algorithm (ordered; first match wins except rule 0)

The algorithm has a **read-only phase** (P1-P4) and a **write phase** (P5+). No byte in `data\` is written before the
read-only phase has excluded rule 1 and rule 2's ABORT branch.

```
P0  take run\<id>.lock (outside data\). Resolve the account dir from the configured binding digest (2).
P1  RULE 0 - scan data\ (and the legacy root) for legacy names. For each: lstat + read-only sha256 (unreadable ->
    "unreadable", non-file -> "non-file"). Report ONCE to incidents\boot.jsonl (outside data\). Never opened for write,
    never moved, never copied into the evidence envelope (decision D15), never parsed. Continue.
P2  RULE 1 - for EVERY file under accounts\<id>\ (members, retained, orphans, HEAD slots, evidence index) and the
    anchor slots: peek file header + record 0 only. frame_version unknown, or any version field not an int >= 0, or
    min_reader_version > R, or writer_seq > R.seq with an unknown format  ->  ABORT-RO.
      ABORT-RO = exit code + one line in incidents\boot.jsonl; no AccountContext; zero writes in data\ (and no anchor
      write). An orphan with a future header also aborts (decision D7: a newer build was writing here).
P3  RULE 2 - hw = max(HEAD.high_water, anchor). If hw.generation > the generation HEAD names (or than any readable
    generation when HEAD is gone), or writer_history shows a lower writer_seq after a higher one:
      hw.writer_seq > R.seq -> ABORT-RO ; else -> HOLD(cause=rollback).   (anchor hard_hold marker -> HOLD too, D11)
P4  RULE 3 - any member unreadable (persistent after 5.5 retries), or a non-regular file/reparse point at a member
    path -> HOLD(cause=io), no access of any kind to that member afterwards. Note which members are unreadable; the
    first write attempt decides soft vs hard HOLD (D6).
P5  RULE 4 - full strict decode of the current generation (snapshot sha256 == HEAD, every record, binding == HEAD
    digest, segments up to sealed_len, prev-hash chain), the retained generations and the evidence index.
      torn tail          -> copy tail to evidence (4.3), seal-and-roll (3.4), continue (not damage; A15, M24)
      damage of any kind -> HOLD(cause=damage): evidence copy of each damaged member (4.3; original untouched),
                            candidate = newest intact retained generation replayed (A07, never managed), ownership
                            UNKNOWN unless proven; a HOLD snapshot G' (4.1) with provenance hold{incident_ids}
      missing member with HEAD/binding/evidence present -> HOLD(cause=missing) (NF-14, M19, M36)
      binding absent/mismatch/other account -> HOLD(cause=identity) (A11, NF-15, NF-46)
P6  RULE 5 - exchange snapshot. Down -> HOLD (at boot nothing was MANAGE in this process); no promotion, no INIT, no
    reconciliation claim; local append-only events stay allowed (A12, M23).
P7  RULE 6 - replay = fold(snapshot, events) (deterministic, 9). If trust MANAGED, binding confirmed, no open HoldItem,
    and the fresh snapshot is a MATCH -> MANAGE. If not a match: for every owned intent with phase unknown/known, query
    by deterministic client id; a FINAL record -> append ResultObserved (E0-E3) -> apply -> re-match. A bare not-found
    proves nothing (A19, NF-47). F<R -> migrate first (4.5), which lands in HOLD by design.
P8  RULE 7 - otherwise HOLD with a per-item list (diff, empty-with-diff, identity, unexplained delta, trivially-empty
    candidate). Each item is a HoldItem{scope: account | symbol/side, cause, evidence refs}.
P9  RULE 8 - nothing exists for this binding (no account dir, no anchor; legacy files do not count): INIT (4.6).
    Interrupted INIT (account dir whose only snapshot is an init G1 and no valid HEAD slot): re-run INIT with
    G' = max seen + 1; orphans reported and kept (D9).
```

Every HOLD entry appends `HoldEntered` (or, if the head itself is damaged, commits the HOLD snapshot). If that write
fails, the account is in **hard HOLD** (6.3). A restart re-derives the same state (A05): damaged bytes are still there
and still damaged; a HOLD snapshot/`HoldEntered` is durable; only R5 of 4.4 writes `trust: MANAGED`.

## 7. Promotion / HOLD state machine

```
                  P2/P3 abort                               P9 flat
   BOOT ───────────────────────────► ABORT_RO (process exits; no AccountContext)
     │ P4/P5/P8 ─────────────► HOLD ◄────────────┐            ┌──────────► MANAGE (known-empty)
     │ P9 non-flat ─► HOLD_INIT ──owner items + recon──┐       │
     │ P6 down ─────► HOLD / INIT_WAIT (no INIT)       ▼       │
     │ P7 match ──────────────────────────────────► MANAGE ◄──┘
     │                                                │  ▲
     │                 damage / diff / identity /     │  │ R4+R5 promotion (writable store, exchange up,
     │                 not-found / owner HOLD         ▼  │ binding confirmed, not trivially empty, fresh re-check)
     │                                              HOLD ┘
     │       any failed store W/F/D (ENOSPC, EROFS, permission, non-file)
     └────────────────────────────────────────► HARD_HOLD (sticky for the process; restart -> BOOT)
```

| State | Store writes | Exchange actions permitted | Refused |
|---|---|---|---|
| ABORT_RO | none in `data\` | none (no AccountContext) | everything |
| INIT_WAIT | none | reads | everything else |
| HOLD_INIT | journal append, HOLD_INIT snapshot | reads; per-item **owner** adopt/close/cancel (journaled first) | entries, adds, auto-adopt, auto-cancel foreign (A22), promotion without recon |
| HOLD | journal append, HOLD snapshot, evidence copy, recon record | reads; journaled PROTECT for owned (decision D12 for candidate-owned) positions; journaled drain of non-protective intents (A20); adopt + protect late fills; reconciliation; owner per-item actions | entries/adds from every source (auto, manual, Telegram, UI, grid, maker); resume/confirm clearing HOLD; auto-cancel foreign |
| HARD_HOLD | none in `data\`; best-effort anchor marker + `incidents\` | **only** the A24 set: (1) narrow query by deterministic id; (2) A23 emergency reduce-only stop, place-confirm-then-cancel-old; (3) idempotent drain of resting ENTRY/ADD by deterministic id, re-query on timeout; (4) adopt + protect race/partial fills reduce-only; (5) external incident | everything else: entries, adds, reprice, fallback, trailing/target/BE moves, grid/maker, market close, cancelling protection for store reasons, journal, reconciliation, promotion |
| MANAGE | all (WAL rules) | all, through NC-06/NC-07 | - |

- HOLD scope (decision D10): an account-level HoldItem blocks the whole account; a symbol/side HoldItem (A19 bare
  not-found, M41) blocks new risk on that symbol/side only. Boot reports `mode=HOLD` if any HoldItem is open.
- Leaving HARD_HOLD: never in-process. Restart -> BOOT; with a writable store the hard-HOLD anchor marker (D11) or the
  exchange difference created by the emergency set forces HOLD -> A08.
- Deterministic client ids (NC-03 owns the format; Binance limit 36 chars `[.A-Z:/a-z0-9_-]`): intents
  `zb-<base32(sha256(account_id|intent_id|attempt))[:24]>`; the emergency stop
  `zb-es-<base32(sha256(account_id|symbol|side|covered_qty_steps))[:20]>` so a restart mid-drain reproduces the same id
  and finds the stop instead of duplicating it (M53).

## 8. Evidence envelope

### 8.1 Format (`.zbe`, file_kind 6)

Header record (cleartext metadata, rtype 1): `{incident_id, sha256 (of plaintext), size, rel_path (relative to data\,
never containing the user name), mtime_ms, created_ms, reason_code, cipher: "dpapi-user-v1" | "aesgcm-v1"}`.
Body record (rtype 30): the ciphertext. End record with the sha256 of the ciphertext.

### 8.2 Cipher. **Recommend (decision D5): per-blob DPAPI, CurrentUser scope, on Windows.**

- `CryptProtectData(plaintext, description="ZackBot NC-02 evidence", entropy=b"zackbot/nc02/evidence/v1|" + account_id,
  flags=CRYPTPROTECT_UI_FORBIDDEN)` through `ctypes` (`crypt32.dll`): stdlib only, no key file to lose or leak, bound to
  the Windows user profile, other local users cannot decrypt. Cost: if an administrator resets the user's password the
  DPAPI master key is lost and the evidence becomes unreadable; acceptable because the originals stay in place until the
  owner resolves the incident, and evidence is forensic, not state.
- Portable (CI on Linux, future hosts): AES-256-GCM via `cryptography` with a 32-byte key in `<base>/keys/evidence.key`
  (0600, dir 0700), nonce 96-bit random per blob, AAD = the cleartext header. Tests may use a `TestOnlyCipher` that the
  production entry point refuses to construct.
- Size cap per blob 64 MiB (store files are small); larger -> metadata-only incident, original untouched.

### 8.3 Access restriction

`set_private_acl(evidence\)` at INIT, before the first blob: protected DACL (no inheritance) with SDDL
`D:P(A;OICI;FA;;;<current-user-SID>)(A;OICI;FA;;;SY)` via `SetNamedSecurityInfoW`. Administrators are excluded
(decision D5). POSIX: 0700. The boot read-only phase re-reads the DACL; drift is an incident, not HOLD.

### 8.4 Exposure rule (A16)

Logs, UI, incidents, reconciliation records and reports carry only: incident id, sha256, size, rel_path, mtime_ms,
reason code, blob id, cipher id. Never the bytes, a prefix, a parse-error excerpt or a decrypted view. Note: a sha256 of a
low-entropy file could confirm a guess; store files and API secrets are high-entropy, so the hash stays exposed as ruled.
Export is an owner tool (decrypt to an explicitly chosen path with a typed confirmation), never automatic.

## 9. Deterministic replay and no-secrets proof

- `replay(snapshot G, segments) -> Portfolio` is a pure fold over NC-01 transitions: no clock reads, no environment, events
  ordered by `lsn`, `prev` chain checked, Decimal context fixed, canonical encoding. Tests: replay twice -> identical
  canonical bytes; at compaction the writer asserts `encode(replay(G-1 + events)) == encode(G)` before S6; property test
  over random event sequences (Hypothesis, seeds recorded per NC-01 contract item 2).
- No secrets: the NC-01 codec rejects unknown fields, so a secret cannot ride along in a known record. The proof test
  writes a store with a test API key/secret configured, runs every write path (INIT, events, compaction, HOLD, recon,
  migration, evidence of a file that *contains* the secret), then scans every byte under `data\`, `anchors\`,
  `incidents\` for the key, the secret and their base64/hex forms: found only inside `.zbe` ciphertext, where it must
  **not** appear in clear either.

## 10. Crash points and fault injection

### 10.1 Fault seam

The harness wraps the real seam in `FaultFs(plan)`. A plan is a list of `(op, path_glob, nth, action)`; actions:
`raise(errno|winerror)`, `crash_before`, `crash_after`, `tear(k)` (only the first k bytes of this write become durable),
`zero_fill(n)` (the file grows by n NUL bytes, the NTFS valid-data-length effect **(inferred)**), `drop_unsynced`.
A crash raises `SimulatedCrash(BaseException)` so `except Exception` in store code cannot swallow it.

### 10.2 Power-loss model

`FaultFs` keeps a shadow per file: the last fsynced content and the pending unsynced writes. At a crash the harness
materialises, for each file, one of: durable content only; durable + a prefix of pending bytes (`tear`); durable +
zero-fill. Directory entries created without `D`: kept under the `ntfs` model, dropped under the `posix` model. Each
crash point runs under both models. Then a **fresh** store instance (new process state) runs recovery against the same
fake exchange and the assertions of `crash_matrix.md`.

### 10.3 Enumeration

1. Record pass: run the scenario (INIT, N events, compaction, a damage-HOLD with evidence, a reconciliation +
   promotion, a migration, a torn-tail seal) with a counting seam; it yields the ordered trace of mutating ops.
2. For every index i in the trace and every action applicable to that op, re-run with the fault at i. That is "kill at
   every write boundary" mechanically, and it cannot silently skip a boundary added later (the trace grows with the
   code). The named points of `crash_matrix.md` (C-E*, C-S*, C-V*, ...) are labels the store emits through a no-op
   `seam.mark("C-S3")` call, so a failing run reports a name, not just an index.
3. Every run also asserts the invariants that hold at every point: never MANAGE with an empty portfolio while the
   exchange differs; never MANAGE on an identity mismatch; no `data\` change after ABORT-RO; no exchange write outside
   the permitted set of the resulting state; evidence originals byte-identical; replay deterministic.

### 10.4 Exchange faults

`FakeExchange` scripts: down, timeout, bare not-found, cancel already-gone, cancel timeout, partial fill before cancel,
fill after cancel (race), foreign order, quantity drift. Rows M30/M31 and M47-M53 need resting orders, which the NF
fixtures' exchange lacks, so they are matrix-runner cases, not fixture cases.

### 10.5 Real-kill drill (Windows)

A smaller set (one per protocol in section 4) runs the store in a child process with `ZB_CRASHPOINT=<name>`; at that mark
the child calls `TerminateProcess` on itself. This does not simulate power loss, but it measures the real NTFS behaviour
of the (inferred) points: whether an `open_new` + `F` without `D` survives, whether a torn slot is observable, and
whether a sharing violation from Defender appears during the run. Results are recorded as measured facts.

## 11. Mapping to acceptance items

| Item | Where in this design |
|---|---|
| A01/A02 | P2 peek-before-decode, zero writes in `data\`, lock + incidents outside `data\`; files are never renamed or truncated, snapshots are write-once |
| A03 | `HEAD.high_water` + `writer_history` + anchor (3.6), P3 |
| A04/A16 | 4.3, section 8 |
| A05 | HOLD is a durable event/snapshot; MANAGED only through 4.4 R5 |
| A06 | `ownership=unknown` unless `known_empty_proof` (3.3) |
| A07 | candidate = replayed retained generation, never managed (P5) |
| A08 | 4.4 |
| A09 | 4.6, P9 |
| A10/A21 | P4, 5.5, hard HOLD on the first failed write |
| A11 | binding record must equal HEAD digest; anchors by-binding |
| A12 | P6 |
| A13 | 4.5 |
| A14 | P1 |
| A15 | 3.4 seal-and-roll (D3) |
| A17 | 3.1 strict parse |
| A18 | S0 / E0 |
| A19 | P7, ResultObserved only from FINAL/REFUSED/corroborated evidence |
| A20/A22/A23/A24 | section 7 table, deterministic ids |

## 12. Decisions needing Codex's call (recommendation first)

| # | Decision | Recommend |
|---|---|---|
| D1 | Commit point | dual-slot in-place `HEAD.a/.b`, no rename anywhere (vs tmp + `MoveFileExW(REPLACE_EXISTING|WRITE_THROUGH)`) |
| D2 | High-water anchor outside `data\` (+ by-binding index); is it part of the store unit? | yes, outside `data\`, written after HEAD, max() rule; not a member for ABORT-RO hashing, but a member for rule 1/2 |
| D3 | Torn-tail repair | seal-and-roll after the evidence copy, no truncate (A15 wording "truncated" would change) |
| D4 | Partial evidence | the store may delete only its own unindexed blob of a failed attempt (exception to R-UNCOMMITTED) |
| D5 | Envelope cipher and ACL | per-blob DPAPI CurrentUser + entropy; DACL user + SYSTEM only (Administrators excluded); AES-GCM key file off Windows |
| D6 | Non-file at a snapshot path while the journal is writable (NF-33) | soft HOLD (journal works); hard HOLD only when the journal or HEAD cannot be written |
| D7 | Orphan with a future header | rule 1 ABORT-RO |
| D8 | "Newer build" ordering | integer `writer_seq`; `writer_build` is display text |
| D9 | Interrupted INIT (G1 init snapshot, no HEAD) | re-INIT (flat -> MANAGE known-empty, non-flat -> HOLD-INIT), orphans kept; anything else without HEAD is damage |
| D10 | HOLD scope | account and symbol/side HoldItems; boot mode HOLD if any open |
| D11 | Hard HOLD durability across restart | best-effort anchor marker; with no marker, the exchange difference decides |
| D12 | PROTECT in HOLD for a position that only the candidate owns (ownership UNKNOWN) | allowed: reduce-only, qty <= min(candidate, exchange), deterministic id, journaled; else owner item |
| D13 | Settings | events + embedded in snapshots, not a separate member |
| D14 | Boot incident log (open Q2) | per install, `incidents\boot.jsonl`, each line carries `account_key` |
| D15 | Legacy files under REJECT | hash + metadata report only; never copied into the envelope |
| D16 | Retention GC of retired generations (crash_matrix C-S10) | journal `GcIntent{files}` and commit a HEAD that drops them, then delete; files named by a durable `GcIntent` are retired, not orphans |
