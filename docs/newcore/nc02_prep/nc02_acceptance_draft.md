# NC-02 recovery/state acceptance - DRAFT r4 (REC-01 retargeted)

Status: draft r4 for Codex (Integration). r1 prepared 2026-10-08 (Cairo) by Claude Code, read-only, on master `cff3f88`;
r2 applies Cowork's cross-check and Codex's eight accepted rulings on it; r3 adds Codex's ruling on open question 4
(emergency protection with an unwritable store, A23); r4 adds Codex's follow-up ruling on what hard HOLD may still do
(the emergency set, A24). See section 9, changelog.
Owner decision (#13): the legacy bot gets no repair. REC-01's direction moves into NEWCORE **NC-02 (state/event store)**.
The legacy behaviour is kept only as **frozen fixtures** (`fixtures/NF-01..49`: 45 negative, 4 positive controls).
This draft proposes no change to legacy code.

Direction carried over from REC-01 (Codex, #13 / PR #34):

- **D1:** a future schema aborts start-up and leaves state and backup byte-identical.
- **D2:** damage to a current-schema file fails closed and keeps the evidence.
- **D3:** an account is never left as an empty managed account.
- **D4:** a rollback/reconciliation matrix is defined, and explicit exchange/state reconciliation comes before management.

Codex rulings already accepted for NEWCORE and binding on every item below: unknown ownership is `None`, never `[]`;
known-empty requires a proven first run plus a flat exchange; a bare not-found proves nothing; manual entries do not bypass
pause or HOLD; no legacy state import (NEWCORE starts flat on testnet); an intent is durable before it is sent and a result
is durable before it is applied; timestamps are integer UTC milliseconds.

**REC/ORD mapping starts at NC-01.** Unknown ownership and result evidence are NC-01 domain types, not NC-02 inventions:
`Ownership.UNKNOWN` (positions/entries/intents are `None`, never empty), `AccountBinding`, `OrderIntent`/`OrderResult`,
`ResultPhase` (`unknown`/`known`/`final`) and `Evidence` (`exchange_final`, `exchange_refused`, `not_found_corroborated`,
`position_adopted`) in `docs/newcore/nc01_prep/nc01_domain_sketch.py`. NC-02 persists and replays these types; it never
re-derives ownership or results from file shapes or position deltas.

## 1. Terms used by the contract

| Term | Meaning |
|---|---|
| Store unit | Everything NC-02 persists for one account: the account binding (marker), the snapshot generations, the append-only intent/result/incident event log, the reconciliation records and the evidence envelope index. It is versioned and validated as **one** unit (legacy `install.json` drifted independently of `state.json`; see NF-05). Legacy files are **not** members (rule 0). |
| Header | Every snapshot and every event segment carries `account_key`, `generation` (monotonic integer), `writer_build`, `format_version` and `min_reader_version` (all JSON integers >= 0 except `account_key`/`writer_build`), and `written_ms` (integer UTC ms). A store-level `high_water` holds the highest generation ever committed and the highest `writer_build`. |
| Committed generation | A generation named by the store's own commit record. Temp, partial and orphan files are never members (R-UNCOMMITTED). |
| Reader R | The running NEWCORE build. `F` is the on-disk format. A header version that is not a JSON integer >= 0 is an **unknown** format, treated as F>R (R-VERSION-TYPE). |
| Reconciliation record | A durable record that binds generation G and the account binding to one exchange snapshot (positions, open orders, protective orders, with their ids and lifecycle states, `taken_ms`). Its verdict is `match` or `owner-resolved` (each difference adopted, closed or cancelled by an explicit owner action, each one recorded). |
| **Match** (R-MATCH) | All of: (1) the snapshot's account equals the store's account binding, and the binding itself is confirmed (not mismatched, not unconfirmed); (2) the snapshot is younger than `MATCH_MAX_AGE_MS` (proposal: 5 000 ms, Codex to set) when the verdict is computed **and again at promotion**; (3) for every symbol/side the aggregate owned quantity (sum of all lots) equals the exchange quantity within the venue step tolerance (`abs(diff) < step/2`); several lots per symbol/side are compared in aggregate and must also each fit their protective orders; (4) every owned order/stop id in the candidate exists on the exchange with the expected lifecycle state (working/triggered/filled), and the protective quantity covers the position; (5) no unowned position and no unowned open order exists. Promotion re-checks (1)-(5) atomically against a fresh snapshot; any change aborts promotion and stays HOLD. |
| Trivially-empty candidate | A candidate with no positions, entries or intents whose emptiness is not proven by R-KNOWN-EMPTY (proven first run + flat exchange, or a journaled result chain from the last non-empty generation). Its ownership is `Ownership.UNKNOWN`. |
| Evidence envelope | Forensic bytes of damaged or rejected members, kept bit-for-bit inside an encrypted, access-restricted envelope outside the live store paths. Only the sha256, size, original path, mtime and the incident id are exposed to logs, UI and reports. |
| Outcomes | **ABORT-RO**: refuse to start this account; no byte of the store is written (no rename, no rotation, no "persist the pause"); report through the exit code and a boot incident log *outside* the store. **HOLD**: the account is loaded into a quarantine view. No new risk from any source. Non-protective open intents are cancelled or drained through the priority queue; a late or partial fill is adopted and protected and stays blocked from new risk until reconciled; protective orders are kept. Durable local append-only intent/result/incident events are allowed. No promotion and no reconciliation claim; only the reconciliation flow (A08) leaves HOLD. **Hard HOLD** (r4): HOLD entered while the store is unwritable (A21/A23). Nothing can be journaled, so the only exchange actions are A23's emergency protective stop and the A24 emergency set; everything else waits for exchange adoption/reconciliation (A08, which needs a writable store). **HOLD-INIT**: HOLD for a store that has never completed INIT. **MANAGE**: normal operation. **INIT**: first-run initialization. **REJECT**: a legacy file is reported and ignored: never read as state, never written, bytes and mtime unchanged. |

## 2. Acceptance list

Each item is a test. Each NF fixture pins the legacy failure the item rejects. Positive controls (legacy got it right;
NC-02 must not regress) are listed in their own column and are never counted as negative evidence.

| ID | Requirement | Pinned by (negative) | Positive controls |
|---|---|---|---|
| NC02-A01 | **Future or unknown format means ABORT-RO.** If any member of the store unit has `min_reader_version > R`, a `writer_build` newer than R with an unknown format, or a version header that is not a JSON integer >= 0, start-up aborts before any write. The sha256 of every file in the data folder is identical before and after, and so are the directory listing and mtimes. Retrying start-up gives the same result. This holds whatever else is wrong with the unit (a damaged primary next to a future backup is ABORT-RO, not HOLD). | NF-01, 02, 03, 04, 05, 06, 22, 23, 24, 25, 29 | |
| NC02-A02 | **A future member is never clobbered.** (r2: narrowed - A01 already covers detection.) No write path (save, rotation, migration, evidence copy, pause persistence, a later start of any build) may rename, overwrite, rotate or truncate a member whose format is future or unknown. Tested by running every write path after an A01 abort and on a store whose only future member is a backup or a non-head event segment. | NF-06, 29 | |
| NC02-A03 | **Generations are monotonic (NEWCORE readers).** A NEWCORE reader that finds `high_water.generation` greater than the best readable generation, or `high_water.writer_build` newer than itself, does not manage: ABORT-RO if the newest writer is newer than R, otherwise HOLD. A store whose writer history shows an older NEWCORE build after a newer one goes to HOLD. A file-level rollback that also rolled `high_water` back must still end in HOLD through the exchange difference (R-ROLLBACK-DETECT). Pre-NEWCORE binaries cannot honour these fields: that gate is the installer's (section 4), not this item's. | NF-48 | |
| NC02-A04 | **Evidence is copied, not moved, and kept in the evidence envelope.** Damaged or rejected bytes are copied bit-for-bit (copy, fsync, verify sha256, then mark) into the encrypted, access-restricted evidence envelope; the original path is never renamed away and then refilled by a rewrite. Only hash and metadata are exposed. If the copy cannot be completed (disk full), the store stays HOLD with the original untouched, and the failure is reported out-of-band; no partial evidence is left. | NF-08, 09, 10, 12, 26, 27, 28, 34, 38 | |
| NC02-A05 | **Fail-closed is durable.** HOLD survives any number of restarts and saves until a reconciliation record exists. A save made while in HOLD cannot produce a "clean" store that the next start trusts. | NF-07, 10, 26 | |
| NC02-A06 | **No empty managed account.** An initialized account (binding, any generation, evidence or reconciliation history) never loads as an empty portfolio: its ownership is `UNKNOWN` (`None`, never `[]`) until proven (R-KNOWN-EMPTY). "Empty" is legal only on INIT with a flat exchange, through a journaled result chain, or after an owner adoption record. An empty portfolio with a differing exchange is HOLD, never MANAGE. | NF-02, 07, 10, 11, 13, 14, 16, 17, 22, 23, 25, 26, 27, 28, 30, 40, 43 | NF-15 |
| NC02-A07 | **A backup is a candidate, never managed state.** An older generation or a replay of the event log is offered as a reconciliation candidate. It becomes managed only after A08 passes. Restoring it never books an exit, P&L or orders. | NF-08, 09, 11, 24, 34, 38 | |
| NC02-A08 | **The reconciliation gate.** Leaving HOLD requires a reconciliation record for the generation being promoted, computed against a fresh snapshot under the **Match** definition (section 1) and re-checked atomically at promotion. Auto-`match` is allowed only when (a) the account binding is confirmed and equals the snapshot's account, and (b) the candidate is not trivially empty. An identity mismatch or a trivially-empty candidate never auto-promotes: both stay HOLD until the owner confirms the binding **and** a fresh reconciliation record exists. Position equality never proves identity. Any difference (extra or missing positions, quantity outside step tolerance, unknown or missing orders, a protective order without a lot) needs a per-item owner action. Resuming entries, toggling pause or confirming the account does **not** clear HOLD. MANUAL, Telegram and UI entry paths go through the same gate. | NF-02, 08, 10, 11, 13, 14, 39, 43, 44, 45, 46 (legacy: the resume gate checks only `install_block`, app.py:1225-1227 and telegram_ctl.py:524; manual entries bypass pause at engine.py:3415) | |
| NC02-A09 | **First run against a non-flat exchange is blocked.** INIT needs a successful exchange snapshot. If it shows positions or open orders, the account is bound only as HOLD-INIT until each item is adopted or closed by the owner. A new or emptied data folder, an interrupted first run, or a folder holding only legacy files never yields "entries open". | NF-16, 17, 39, 49 | |
| NC02-A10 | **Unreadable is not damaged.** A persistent I/O failure (sharing violation, permission, a directory or other non-file at a store path) leaves the path untouched and never saved over, and the account stays HOLD. Legacy got the "untouched" part right; NC-02 must also stop it from becoming an empty account. | NF-13, 33 | |
| NC02-A11 | **The store unit is one source of truth.** If the binding and the snapshots disagree (missing, invalid, other account), the result is HOLD, and per A08 it cannot auto-promote. A backup of the binding can never silently clear an owner-confirmation requirement. | NF-05, 46 | NF-15 |
| NC02-A12 | **Exchange unreachable.** While no exchange snapshot can be taken, there is no promotion, no INIT and no reconciliation claim. Durable local append-only intent, result and incident events stay allowed in every state; ownership stays HOLD (or MANAGE continues protective management when it already was MANAGE; NC-03/NC-04 own that). | matrix row M23 | |
| NC02-A13 | **Migrations are additive and generational.** Migrating NEWCORE F older to R writes a **new** generation alongside, never in place. The prior generation stays readable by its own build until R is confirmed (installer acceptance) and a reconciliation record exists for the migrated generation. A down-migration exists only as an explicit owner tool. | installer drill (section 4) | NF-21 (legacy v0 to v1 with confirm) |
| NC02-A14 | **No legacy import: REJECT.** (r2: replaces "legacy import is one way", Codex ruling 10.) NEWCORE never reads legacy `state.json`/`settings.json`/`install.json` (or their `.bak`/`.corrupt-*`) as state, settings or binding. If present they are REJECTed: reported once with their sha256, left byte- and mtime-identical, never moved. NEWCORE then starts by INIT on testnet (flat exchange: MANAGE empty; non-flat: HOLD-INIT). The installer/launcher refuses a rollback across the NEWCORE boundary unless the owner explicitly asks for it (section 4). | NF-49, and every NF fixture as REJECT input (section 5.1a) | |
| NC02-A15 | **A torn tail is not damage, and repair never destroys bytes.** An incomplete final event record (crash mid-append) is first **copied into the evidence envelope**, then truncated to the last complete record and reported. Damage before the tail is A04/A05 damage. Uncommitted temp/partial files are never read as state, never silently deleted, and reported (R-UNCOMMITTED). The crash matrix covers every write boundary (as in the plan's C2). | NF-36 | NF-37 |
| NC02-A16 | **No credentials anywhere.** NEWCORE state, events, reconciliation records and incident logs never contain credentials (carries over AUD-05 r2-r5). Forensic bytes - which may come from a damaged or foreign file that does contain secrets - are kept exactly, but only inside the encrypted, access-restricted evidence envelope; everything outside it carries hash and metadata only. A04 and A16 therefore do not conflict. | existing AUD-05 tests as the spec source | |
| NC02-A17 | **Strict, bounded parsing; controlled failure.** (new) Duplicate object keys, NaN/Infinity, numbers outside declared ranges (quantity above the venue maximum, integers outside int64), bytes after the document (NUL padding included) and all-NUL files are damage (rule 4), never a valid document and never an uncaught exception. | NF-27, 28, 30, 31, 32 | |
| NC02-A18 | **The writer validates like the reader.** (new) Every record is validated with the reader schema before commit. An order intent needs kind, client id, `created_ms` (integer UTC ms) and a quantity > 0, a whole number of venue steps and <= the venue maximum; any other shape is refused at write time (nothing is sent) and is damage at read time. | NF-40, 41, 42, 31 | |
| NC02-A19 | **Results come from evidence, not deltas.** (new) Ownership changes only through a durable result record (NC-01 `OrderResult` with `ResultPhase.FINAL` and `Evidence`). A bare not-found never resolves an intent (`not_found_corroborated` needs the NC-01 window); reconcile never books a stop/resync/close at a guessed price from a position delta; an unexplained delta is a HOLD item. | NF-36, 41, 45, 47 | |
| NC02-A20 | **HOLD drains, never abandons.** (new, Codex ruling 3) Entering HOLD cancels or drains non-protective open intents (resting maker entries, pending adds, grid cells) through the priority queue; protective orders stay. A late or partial fill that lands during HOLD is adopted as an owned position, protected, recorded as a HOLD item, and blocked from new risk until reconciled. (r4: with the store unwritable the drain cannot be journaled first; A24 governs it.) | covered by matrix rows M30-M31 (no legacy fixture: the fake exchange has no resting orders) | |
| NC02-A21 | **Store I/O faults block sends.** (new) ENOSPC, EROFS, permission or a non-file at a store path means no intent can be made durable, so nothing is sent (protective orders already on the exchange stay); it is reported out-of-band; no partial generation or evidence is left. (r3: the only exception is the emergency protective stop of A23. r4: A23's single companion exception, the A24 hard-HOLD emergency set, is the only other; nothing else is sent.) | NF-33, 34 | NF-35 |
| NC02-A22 | **Foreign orders are never auto-handled.** (new) An unknown position or a foreign order is never cancelled, closed or adopted automatically; it is a per-item owner decision while HOLD. | NF-44 | |
| NC02-A23 | **Protection outranks persistence.** (r3, Codex ruling on open question 4) When the durable journal is unavailable (A21: ENOSPC, EROFS, permission, a non-file at a store path), NEWCORE may place or repair a **reduce-only protective stop with a deterministic client id** for an **owned** position, as the single exception to "an intent is durable before it is sent". Conditions: it never removes confirmed protection before the replacement is confirmed on the exchange (place new, confirm, only then cancel old); it never increases exposure (reduce-only, quantity <= the owned position). It then immediately enters **hard HOLD**, emits an external incident best-effort (outside the store), and requires exchange adoption/reconciliation before any action outside the **A24 emergency set**, which is this item's only companion exception (r4; r3 said "before any other action"). No entry, add or ordinary management action (trailing, target moves, grid/maker work) gets this exception; they are refused. Protection that is already confirmed and still covers the owned position is left unchanged. | matrix rows M43-M46 (no legacy negative: legacy sends nothing at all) | NF-35 (protection already confirmed: no change, HOLD; required behaviour corrected in section 5b) |
| NC02-A24 | **Hard-HOLD emergency set.** (r4, Codex follow-up ruling) The only companion exception to A23. "Before any other action" in A23 means no ordinary lifecycle action, not "leave future exposure resting". While the durable journal is unavailable, hard HOLD may perform **only** these five actions: (1) query the exchange narrowly enough to identify already-owned protective and risk-adding intents (by deterministic client id / owned order id; reads only, foreign orders stay A22 items); (2) place and confirm any needed deterministic-client-id, reduce-only protection **before** removing old protection (A23); (3) cancel/drain resting ENTRY/ADD intents idempotently by their deterministic client id - a cancel that succeeds or finds the order already gone is done, a cancel that times out or returns an unknown result is re-queried (never assumed either way) while HOLD continues; (4) adopt any race or partial fill from exchange truth and protect it without increasing exposure (reduce-only, quantity <= the adopted position), and remain HOLD; (5) emit the external incident best-effort. **Forbidden** in hard HOLD: cancelling a protective order merely because the store is unavailable (old protection is cancelled only after a covering replacement is confirmed, A23); repricing an entry or any fallback (re-post, maker-to-taker, chase); ordinary management (trailing, target or break-even moves, grid/maker work, closing at market); resuming risk or sending any entry/add. Nothing done here is a durable result or a reconciliation claim (A19): every restart repeats the reconciliation safely from exchange truth, and deterministic client ids make re-cancel and re-protect idempotent (no duplicate stop, no exposure increase). Leaving hard HOLD needs a writable store and A08. | matrix rows M47-M53 (no legacy negative: legacy sends nothing; the fake exchange has no resting orders) | |

## 3. Rollback/reconciliation matrix (NEWCORE reader rules)

These rules are for the **NEWCORE reader** only. Rollback to pre-NEWCORE binaries is an **installer** concern with its own
rules and drill (section 4); shipped legacy builds cannot read `generation`/`min_reader_version`/`high_water`.

The rules are applied in order, and the first match wins (rule 0 does not end the evaluation). R is the reader build. For F:
"F<R" means migratable, "F=R" current, "F>R" future (`min_reader_version > R`) and "F?" unknown (non-integer version
header). The exchange states are **flat**, **=state** (matches the newest trusted candidate under R-MATCH), **diff** (any
difference) and **down** (no snapshot possible).

### 3a. Rules

0. Legacy files are present (`state.json`, `settings.json`, `install.json`, their `.bak`/`.corrupt-*`): **REJECT** them (A14), then continue with the NEWCORE store alone.
1. Any member of the unit has F>R or F?, or a newer `writer_build` with an unknown format (in the primary, backup or event segment): **ABORT-RO**, whatever the damage or exchange state (a damaged primary next to a future backup is rule 1).
2. `high_water.generation` is greater than the best readable generation, or the history shows an older NEWCORE writer after a newer one: **ABORT-RO** if the newest writer is newer than R, otherwise **HOLD**.
3. Any member is unreadable (I/O, or a non-file at a member path): **HOLD**, with no writes to that member (A10, A21).
4. Any damage (including A17 parse rejections and A18 invalid records), a missing member with the binding or evidence present, or a binding/snapshot mismatch: **HOLD**, with evidence copied (A04) and the best intact generation offered as a candidate (A07).
5. The exchange is **down**: no promotion, no INIT, no reconciliation claim. A store that was MANAGE in this process continues protective-only management (NC-03/NC-04); otherwise **HOLD**. Local append-only intent/result/incident events are allowed (A12).
6. The store is intact, F=R or F<R, its account binding is confirmed, its last reconciliation record covers the current generation, and a fresh snapshot is a **match**: **MANAGE** (F<R migrates first as a new generation, A13). Differences that durable result records or final records of owned order ids explain are applied as results (A19), then re-matched; anything else is rule 7.
7. The store is intact but there is no reconciliation record for the current generation, or the fresh snapshot is not a match (diff, an empty portfolio with a differing exchange, an identity mismatch): **HOLD** until A08.
8. Nothing at all exists (no binding, generation or evidence; legacy files do not count, rule 0): **INIT**. A flat exchange gives MANAGE with a known-empty portfolio. A diff exchange gives **HOLD-INIT** until each item is adopted or closed. A down exchange means INIT waits and nothing is written.

From HOLD: auto-`match` is allowed only under A08 (binding confirmed and equal, candidate not trivially empty, fresh
snapshot, atomic re-check). Open question 1 asks whether the owner may require a confirmation even then. On diff, the account
stays in HOLD with a per-item list. On down, it stays in HOLD.

### 3b. Representative cells (one row per test)

Rows M06/M07 moved to the installer table (section 4, rows I01/I02) in r2; their IDs are not reused.

| # | Reader | F | Damage | Exchange | Expected (rule) | Legacy observed (fixture) |
|---|---|---|---|---|---|---|
| M01 | N | F>R primary only | intact | any | ABORT-RO, bytes identical (1) | moved aside, older .bak managed, .bak overwritten after 2 saves (NF-01) |
| M02 | N | F>R primary + backup | intact | diff | ABORT-RO (1) | both moved aside, 0 lots, entry sent (NF-02) |
| M03 | N | F>R backup only | intact | =state | ABORT-RO (1) | silent, .bak overwritten (NF-06) |
| M04 | N | F>R settings | intact | any | ABORT-RO (1) | rewritten from .bak or from defaults (NF-03/04) |
| M05 | N | F>R binding | intact | any | ABORT-RO (1) | moved aside, then silently restored from .bak on the next restart (NF-05) |
| M08 | N | F=R, older NEWCORE writer ran after a newer one | intact | =state | HOLD then A08 (2) | undetectable in legacy (NF-18 d3, installer context) |
| M09 | N | F=R | intact, reconciled | =state | MANAGE (6) | MANAGE (OK) |
| M10 | N | F=R | intact, reconciled, **non-empty** | diff explained by final records of owned order ids (e.g. own stop filled while offline) | apply results (A19), re-match, then MANAGE (6) | stop booked from the position delta at the stop price (NF-36 shows the wrong-booking risk) |
| M10b | N | F=R | intact, reconciled | diff not explained, **or** empty portfolio with any diff | HOLD with a per-item list (7) - never MANAGE | untracked after 2 passes, entries open (NF-44, 45, 48) |
| M11 | N | F<R | intact | =state | migrate as a new generation, then HOLD, then auto `match` (A08), then MANAGE | (legacy v0 to v1 in place, positive control NF-21) |
| M12 | N | F<R | intact | diff | migrate as a new generation, then HOLD with a per-item list | install_mismatch only when the state owns something |
| M13 | N | F=R | primary damaged, backup intact | =candidate | HOLD, evidence copied, auto `match` only if binding confirmed and candidate not trivially empty, then MANAGE (4) | backup promoted directly; resume allowed with no check (NF-08/09) |
| M14 | N | F=R | primary damaged, backup intact but older | diff | HOLD; candidate shown with its diff (4) | backup promoted, entry sent (NF-11) |
| M15 | N | F=R | both damaged | flat | HOLD; ownership UNKNOWN; the owner may adopt "empty" explicitly; never auto-match (4) | defaults or empty, resume allowed (NF-27) |
| M16 | N | F=R | both damaged | diff | HOLD (never an empty managed account) (4) | 0 lots, entry sent (NF-10) |
| M17 | N | F=R | both damaged, then save and restart | diff | still HOLD (A05) | clean empty start (NF-07) |
| M18 | N | F=R | primary unreadable (I/O) | diff | HOLD, file untouched (3) | untouched, but 0 lots and resume allowed (NF-13) |
| M19 | N | F=R | snapshot missing, binding present | diff | HOLD (4) | failed_closed, but resume allowed, entry sent (NF-14) |
| M20 | N | F=R | binding missing, snapshot present | any | HOLD (A11); no auto-match | install_block (positive control NF-15) |
| M21 | N | none | nothing exists | flat | INIT then MANAGE (known-empty) (8) | INIT (OK) |
| M22 | N | none | nothing exists | diff | HOLD-INIT (8) | INIT, entries open, entry sent (NF-16/17) |
| M23 | N | any | any | down | rule 5: no promotion, no INIT, no reconciliation claim; durable local append-only intent/result/incident events allowed; ownership stays HOLD (MANAGE continues protective-only if already MANAGE) | n/a |
| M24 | N | F=R | event log torn tail | =state | copy the tail into the evidence envelope, truncate to the last record, report, then the rules above (A15) | n/a (new in NC-02) |
| M25 | N | F=R | event log mid-segment damage | any | HOLD, evidence copied (4) | n/a |
| M26 | N | legacy files only (intact) | intact | =state | REJECT legacy (0), then INIT: non-flat exchange gives HOLD-INIT (8); legacy bytes and mtimes unchanged | legacy manages its own files (NF-49) |
| M27 | N | legacy files only (damaged) | damaged | any | REJECT legacy (0); never read, never repaired, never copied into NEWCORE state; then INIT per exchange (8) | n/a (r1 cited NF-20 here by mistake: NF-20 is an old build reading damaged current files) |
| M28 | N | F>R, no backup | intact | diff | ABORT-RO (1) | moved aside, 0 lots, entry sent (NF-22) |
| M29 | N | primary damaged + backup F>R | damaged + future | diff | ABORT-RO (1 wins over 4); damaged primary untouched too | both moved aside, 0 lots, entry sent (NF-29) |
| M30 | N | F=R | intact | diff: a non-protective resting order of the account fills during HOLD | adopt the fill, protect it, record a HOLD item, stay blocked (A20) | n/a (fake exchange has no resting orders) |
| M31 | N | F=R | any HOLD cause | non-protective resting entry working | cancel/drain through the priority queue; protective orders kept (A20) | n/a |
| M32 | N | F? (string/float/bool/null/negative header) | intact | diff | ABORT-RO (1) | handled as damage; .bak promoted or 0 lots (NF-23, 24, 25) |
| M33 | N | F=R | hostile JSON (duplicate key, NaN, huge integer, trailing NUL) | diff | HOLD with evidence, candidate offered (4, A17) | duplicate key loads EMPTY silently (NF-30); NaN loads silently (NF-31); OverflowError crash (NF-32) |
| M34 | N | F=R | store path is a directory / ENOSPC / EROFS | =state or diff | HOLD, nothing sent, nothing partial (3, A21) | 0 lots (NF-33); .bak promoted in memory (NF-34); new risk blocked (positive control NF-35) |
| M35 | N | F=R | committed G + uncommitted temp (complete or torn) | flat / =state | temp never read, reported, kept; complete newer temp with a diff: HOLD (7); torn temp with =state: MANAGE (6) | stop booked at a guessed price (NF-36); torn temp ignored (positive control NF-37) |
| M36 | N | F=R | G missing, evidence of G present, G-1 intact (crash after copy/mark) | =candidate | HOLD, evidence linked to the incident, candidate offered (4) | older .bak managed, evidence unlinked (NF-38) |
| M37 | N | F=R | first run interrupted (no binding committed) | diff (unknown position) | HOLD-INIT (8); confirming the account alone does not clear it | confirm_install re-opened entries with the position untracked (NF-39) |
| M38 | N | F=R | invalid intent in the committed snapshot (qty-only, qty 0, qty out of range) | =state | HOLD (4, A18); no booking, no stop replacement from it | rejected after save accepted it (NF-40); 0-qty "fill" booked + stop replaced (NF-41); 1e300 persisted (NF-42) |
| M39 | N | F=R | valid empty G, G-1 owns lots, no journaled result chain | flat | HOLD; ownership UNKNOWN; no auto-match (7, R-KNOWN-EMPTY) | loads clean, .bak with the lot overwritten after 2 saves (NF-43) |
| M40 | N | F=R | intact | identical positions, binding names another account | HOLD (7, A08/A11); binding confirmation **and** fresh reconciliation required | one confirm re-opened entries (NF-46) |
| M41 | N | F=R | committed close intent, lookup answers bare not-found | position changed | HOLD for that symbol/side; result UNKNOWN until final record or NC-01 corroboration (A19) | pending dropped as "never reached", resync booked at market (NF-47) |
| M42 | N | F=R (rolled back) | whole store replaced by an older valid generation | diff | HOLD (2 if `high_water` survived, else 7 via the diff) | loads clean, 0.5 untracked, entries open (NF-48) |
| M43 | N | F=R | store unwritable (ENOSPC / EROFS / permission / non-file at a member path) | owned position, **no** confirmed protective stop | emergency reduce-only stop with a deterministic client id (quantity <= the owned position), then hard HOLD + external incident best-effort; exchange adoption/reconciliation before any action outside the A24 emergency set (3, A21, A23, A24) | n/a (legacy sends nothing; no fixture: NF-35's exchange already holds the stop) |
| M44 | N | F=R | store unwritable | an entry or add is wanted (any source: auto, manual, Telegram, UI, grid, maker) | refused, nothing sent; hard HOLD + incident (3, A21, A23) | persist_block refuses auto and manual entries (positive control NF-35) |
| M45 | N | F=R | store unwritable | owned position, protection already confirmed and covering it | no change to the protection; hard HOLD + incident (3, A23); r4: resting ENTRY/ADD intents, if any, are still drained per A24 (rows M47-M53), so "no exchange write" holds only when none rest | nothing sent, new risk blocked (positive control NF-35) |
| M46 | N | F=R | store unwritable | confirmed protective stop needs replacement (e.g. no longer covers the owned quantity) | place the new reduce-only stop (deterministic client id) first; cancel the old one only after the replacement is confirmed on the exchange; if the replacement is not confirmed, the old stop stays; never increases exposure; hard HOLD + incident (3, A23) | n/a |
| M47 | N | F=R | store unwritable | owned resting ENTRY/ADD intent working; the cancel succeeds | hard HOLD; narrow query (A24.1), cancel by deterministic client id (A24.3), the re-query confirms it cancelled; no reprice, no fallback; protection untouched; incident (3, A24) | n/a (fake exchange has no resting orders) |
| M48 | N | F=R | store unwritable | owned resting ENTRY/ADD intent; the cancel answers "already gone" | the cancel counts as done (idempotent, no retry storm); positions and the order are re-queried by deterministic id: a fill that shows is adopted and protected (M50/M51), otherwise the intent's result stays UNKNOWN (a bare not-found proves nothing, A19); hard HOLD (3, A24) | n/a |
| M49 | N | F=R | store unwritable | owned resting ENTRY/ADD intent; the cancel times out or its result is unknown | assume neither success nor failure; re-query by deterministic id (re-cancel only while it still shows working); never re-sent as a new entry; if still unresolved, the result stays UNKNOWN and hard HOLD persists (3, A24) | n/a |
| M50 | N | F=R | store unwritable | owned resting ENTRY/ADD partially filled before the cancel | cancel the remainder (never re-posted); adopt the filled quantity from exchange truth; protect exactly the adopted position reduce-only with a deterministic client id (new covering stop confirmed before any old stop is cancelled, A23); exposure never increases; hard HOLD (3, A23, A24) | n/a |
| M51 | N | F=R | store unwritable | owned resting ENTRY/ADD fills after the cancel was sent (the cancel lost the race) | the exchange fill wins over the cancel answer; adopt it from exchange truth and protect it as in M50; no close at market, no management; hard HOLD (3, A23, A24) | n/a |
| M52 | N | F=R | store unwritable | an owned protective order is working while ENTRY/ADD intents are drained | the protective order stays: never cancelled because the store is unavailable; it is replaced only per A23/M46 (e.g. it no longer covers after an adopted fill: new covering stop confirmed first, then the old one cancelled); hard HOLD (3, A23, A24) | n/a |
| M53 | N | F=R | store unwritable; the process restarts mid-drain | any of M47-M52 half done | nothing was journaled, so nothing from the earlier process is trusted: hard HOLD again (3, A21) and the A24 set repeats from exchange truth; deterministic client ids make it idempotent (already-gone cancels are no-ops, an existing stop with the same client id is confirmed, not duplicated); no exposure increase. If the store is writable again on restart, the normal HOLD path (A20, A08) applies and results come only from exchange evidence (A19) | n/a |

**Cross-cutting for every HOLD row:** MANUAL, Telegram and UI entries are refused, and resuming entries does not clear HOLD.
Legacy breaks both: the manual path skips the pause (engine.py:3415), and resume consults only `install_block`.
Rows M43-M53 are **hard HOLD** (A23/A24): outside A23's emergency stop and the A24 emergency set, no exchange action until
adoption/reconciliation. (r4 correction: r3 said "no exchange action of any kind", which would have left resting
ENTRY/ADD exposure working; Codex's follow-up ruling replaced that reading.)

## 4. Installer/rollback interaction (installer rules, separate from section 3)

Pre-NEWCORE (legacy) binaries cannot honour `generation`, `min_reader_version` or `high_water`, so no NEWCORE reader rule
can protect a rollback to them. That gate belongs to the **installer**, proven by an **installer compatibility simulation**
with a simulated pre-NEWCORE reader.

| # | Installed build after the step | Data present | Expected (installer rule) | Legacy observed (fixture) |
|---|---|---|---|---|
| I01 | pre-NEWCORE N-1 (r1 row M06; r1 cited rule 2, it is the installer analogue of rule 1) | files written by a newer build | the installer refuses the rollback unless the owner explicitly asks; with the owner's consent, N-1 starts on its own last files and the launcher shows that they are stale versus the exchange | OLD manages and rewrites NEW files (NF-18) |
| I02 | pre-NEWCORE N-1 (r1 row M07) | N+1 (v2) files | as I01 | OLD launders the v2 label (NF-19) |
| I03 | pre-NEWCORE N-1 | damaged current files | as I01; the installer never lets N-1 start on damaged files it would overwrite | OLD starts empty and overwrites the evidence (NF-20) |
| I04 | NEWCORE N after NEWCORE N+1 wrote G+1 | NEWCORE store | N reads `high_water`: rule 2 (ABORT-RO), or HOLD on its own generation G if the owner chose "keep N generation"; never manages G silently | n/a |

- The legacy installer rollback (installer.ps1 `Invoke-Rollback`) restores the **exe only**. NC-02 therefore requires A13:
  generational writes and no in-place migration before installer acceptance.
- Rollback drill: build N writes generation G+1, the installer rolls back to the previous build, and the drill asserts
  I01-I04 with the simulated reader. NF-18/19/20 are installer evidence; they are **not** A01 evidence (r1 listed NF-18/19
  under A01, which no NEWCORE reader can satisfy for a pre-NEWCORE binary).

## 5. Regression tests to land with NC-02

1. **Fixture pack runner.** Before anything else it runs `repro/verify_fixtures.py` (contracts and input bytes). For each
   `fixtures/NF-*` it copies **exactly** the files named in `input_files` (sha256-checked; nothing else, so NF-16's
   `.gitkeep` is never copied and NF-16 starts from a truly empty folder), creates `input/` if git dropped it, materialises
   `layout` (NF-33: `state.json` is a directory), applies `inject` (NF-34 ENOSPC, NF-35 EROFS) through the store's
   filesystem fault seam, and **sets every input mtime** - from `input_mtimes_ms` for v2 fixtures, or to the fixed
   `1791400000000` ms for v1 fixtures - because git keeps no mtimes and A01 compares them. The fake exchange is loaded from
   `exchange` (positions, protective orders, `order_lookup` behaviour). Two uses per fixture:
   a. **REJECT check (A14):** the inputs placed where legacy kept them; NEWCORE must leave every byte and mtime unchanged
      and derive no ownership from them.
   b. **Scenario check:** the runner builds the equivalent NEWCORE store with the store's own test writer from
      `nc02_scenario` (v2 fixtures) or the table in section 5a (NF-01..21), applies the same damage, starts NC-02 and
      asserts the outcome (`nc02_outcome`, or section 5a), the `nc02_required` ids, no AccountContext for ABORT-RO, no
      exchange writes other than HOLD draining (A20), the A23 emergency protective stop and the A24 emergency set, and
      the same outcome after a restart.
2. **Matrix runner.** It parametrizes M01-M53 (minus M06/M07) from a table (reader x F x damage x exchange) and checks the
   outcome and the "no write" set.
3. **Gate tests.** In HOLD, every entry source (auto, manual, Telegram, trailing, grid, maker) is refused, and
   resume/confirm do not clear HOLD (A08).
4. **Crash matrix (from the plan's C2).** Kill at every write boundary of snapshot, event append, reconciliation record,
   migration and evidence copy. After restart, the outcome follows the matrix, and no state is half-promoted. NF-36..39 pin
   four of these boundaries in legacy terms (D01-D04).
5. **Installer compatibility simulation (section 4)** with a simulated pre-NEWCORE reader (I01-I04).
6. **Property test.** For random store units with an injected future, unknown or damaged member, a random binding and a
   random exchange, the outcome is never MANAGE with an empty portfolio while the exchange differs, never MANAGE with an
   identity mismatch, and never an auto-promotion of a trivially-empty candidate.

### 5a. Scenario and outcome for the frozen fixtures NF-01..21

Their `fixture.json` files are frozen (contract v1), so their NEWCORE scenario and expected outcome live here.

| Fixture | NEWCORE scenario | Outcome | Kind |
|---|---|---|---|
| NF-01 | G has F>R, G-1 current | ABORT-RO | negative |
| NF-02 | G and G-1 have F>R; exchange non-flat | ABORT-RO | negative |
| NF-03 | settings member F>R, its backup current | ABORT-RO | negative |
| NF-04 | settings member and backup F>R | ABORT-RO | negative |
| NF-05 | binding F>R, binding backup current | ABORT-RO | negative |
| NF-06 | G current, G-1 F>R | ABORT-RO | negative |
| NF-07 | NF-02, then one save and a restart | ABORT-RO both times (no save happens) | negative |
| NF-08 | G truncated, G-1 intact | HOLD, candidate G-1 | negative |
| NF-09 | G schema-invalid, G-1 intact | HOLD, candidate G-1 | negative |
| NF-10 | G and G-1 schema-invalid | HOLD, ownership UNKNOWN | negative |
| NF-11 | G truncated, G-1 intact but older and empty | HOLD, G-1 candidate is trivially empty (no auto-match) | negative |
| NF-12 | settings member and backup truncated | HOLD | negative |
| NF-13 | G persistently unreadable | HOLD, G untouched | negative |
| NF-14 | all generations missing, binding present | HOLD, ownership UNKNOWN | negative |
| NF-15 | binding missing, versioned generations present | HOLD (A11) | positive control |
| NF-16 | empty data folder, exchange non-flat | HOLD-INIT | negative |
| NF-17 | only legacy v0 settings, exchange non-flat | REJECT legacy, HOLD-INIT | negative |
| NF-18 | installer row I01 | installer refuses rollback | negative (installer) |
| NF-19 | installer row I02 | installer refuses rollback | negative (installer) |
| NF-20 | installer row I03 | installer refuses rollback | negative (installer) |
| NF-21 | legacy v0 files, legacy reader | REJECT for NEWCORE (A14); legacy migration with confirm stays a positive control for A13's "confirm before trust" | positive control |

Note on frozen requirement text: the v1 requirement `R-NO-MANAGE` says "no ... journal write". For HOLD outcomes, Codex
ruling 6 overrides that wording: durable local append-only intent/result/incident events are allowed (R-HOLD); for
ABORT-RO it stays exact (zero store writes). This is recorded as a correction in the ledger (section 6), not by editing the
frozen fixtures.

### 5b. Corrections to frozen v2 fixtures (NF-22+)

Their `fixture.json` contracts are frozen too (MANIFEST hashes `nc02_outcome` and `nc02_required`), so a ruling that
changes their required NC-02 behaviour is recorded here and as a `CORRECTIONS.json` entry (section 6). The fixture files
are not edited.

| Fixture | Frozen scenario / outcome | Required NC-02 behaviour now | Source |
|---|---|---|---|
| NF-35 | intact reconciled store, store directory read-only (EROFS) / `HOLD`; `R-IO-FAULT` says "nothing is sent" | **emergency protection + hard HOLD** (A23): an owned position without confirmed protection gets a deterministic-client-id, reduce-only stop; confirmed protection is never removed before its replacement is confirmed; exposure never increases; then hard HOLD, an external incident best-effort, and exchange adoption/reconciliation before any action outside the A24 emergency set (narrow query, protect-before-remove, idempotent drain of resting ENTRY/ADD intents, adopt and protect race/partial fills, incident). NF-35's own exchange already holds a confirmed stop covering the 1.0 BTCUSDT LONG and has no resting ENTRY/ADD order, so the drain set is empty and the assertion is unchanged: no exchange write (A24 reads allowed), the stop not cancelled, hard HOLD, incident emitted (row M45). Entries/adds stay refused (row M44). r4: `R-HOLD`'s "drained through the priority queue" is read through A24 when the store is unwritable (the drain cannot be journaled first). | Codex ruling on open question 4 (r3); follow-up ruling on the hard-HOLD emergency set (r4) |

## 6. Reusing the golden/ledger approach

- Use the AUD-07 pack mechanics for the fixtures: `MANIFEST.json` holds the sha256 of each fixture **contract**. v1
  (NF-01..21) hashes `id`, `reader_build`, `input_files`, `exchange`, `nc02_required`; v2 (NF-22+) also hashes `kind`,
  `input_mtimes_ms`, `layout`, `inject` and `nc02_outcome` (`MANIFEST.contract_fields` / `contract_version`). Add an
  append-only, hash-chained `CORRECTIONS.json` in which every NC-02 behaviour that differs from `legacy_observed`, and every
  superseded requirement wording (R-NO-MANAGE journaling, section 5a), is an accepted correction entry. That prevents silent
  rebaselining.
- The `legacy_observed` text is documentation, not an assertion. The legacy engine is not run in NC-02 CI.
- The fixture inputs are generated (`make_fixtures.py`, `fix_nf21.py` for the pre-AUD-05 writer, and for NF-22+
  `repro_e_nc02_ext.py` staging the bytes it runs legacy on, then `make_fixtures_ext.py`), and then frozen as bytes. Their
  hashes are the contract, so regenerating them is a ledger event. `make_fixtures_ext.py` refuses to touch NF-01..21 or to
  overwrite any existing fixture; `fixtures/.gitattributes` marks every fixture file `-text` so no checkout rewrites line
  endings.

## 7. Open questions for Codex

1. Under A08's restrictions (binding confirmed, not trivially empty, fresh snapshot, atomic re-check), is the auto-`match`
   exit from HOLD acceptable, or does every HOLD exit need an owner confirmation?
2. Should the boot incident log outside the store (needed for ABORT-RO with zero store writes) be per account or per
   install?
3. `MATCH_MAX_AGE_MS` and the step tolerance: proposal 5 000 ms and `abs(diff) < step/2`.
4. **Resolved (r3).** With the store unwritable (A21, NF-35), may NC-03/NC-04 still send a protective repair whose intent
   cannot be made durable? Codex ruling: "Protection outranks persistence. NEWCORE may place or repair a
   deterministic-client-id, reduce-only protective stop when the durable journal is unavailable, provided it never removes
   confirmed protection before replacement is confirmed and never increases exposure. Immediately enter hard HOLD, emit an
   external incident best-effort, and require exchange adoption/reconciliation before any other action. No entry/add/ordinary
   management action gets this exception." Incorporated as A23, rows M43-M46, and the NF-35 correction (section 5b).
   r4 follow-up ruling: "before any other action" means no ordinary lifecycle action, not "leave future exposure
   resting"; hard HOLD may perform only the five-step emergency set (narrow query, protect before removing, idempotent
   ENTRY/ADD drain, adopt and protect race/partial fills, incident) and every restart repeats reconciliation from exchange
   truth. Incorporated as A24 (A23's only companion exception) and rows M47-M53.
5. Symlinks/reparse points at store paths (Cowork C02/C03): proposal - treat any non-regular file at a member path as rule 3
   (HOLD, never followed). No fixture (section 8).

## 8. Fixture coverage of the Cowork cross-check

| Cowork case | Fixture | Notes |
|---|---|---|
| A06 future, no .bak | NF-22 | |
| A08 non-int schema_version | NF-23 ("2", true), NF-24 (1.0), NF-25 (null, -1) | |
| B05-B07 damaged primary, backup missing/damaged, NUL | NF-26, NF-27, NF-28 | B08 = damaged primary + **future** backup: NF-29 |
| B13 duplicate "lots" | NF-30 | silent empty in legacy |
| B14 NaN | NF-31 | |
| B15 10**400 | NF-32 | uncaught OverflowError at engine.py:518 |
| C01 state.json is a directory | NF-33 | `layout` |
| C02/C03 symlinks | **skipped** | not portable: git on Windows stores links as text files unless `core.symlinks`, and creating one here fails with WinError 1314 (no privilege), so no legacy run could be observed; covered by open question 5 |
| C05 ENOSPC | NF-34 | `inject` |
| C06/C07 EROFS | NF-35 | positive control (legacy blocked new risk); required NC-02 behaviour corrected by A23/A24 (section 5b) |
| D01-D04 crash points | NF-36, NF-37 (positive control), NF-38, NF-39 | D-mapping is Claude's: D01 orphan complete temp, D02 torn temp, D03 crash after move-aside, D04 interrupted first run |
| E01/E05/E10 qty-only + save accepts | NF-40 | |
| E02 qty 0 | NF-41 | |
| E03 qty 1e300 | NF-42 | |
| F05 valid empty vs .bak with lots | NF-43 | |
| review 8: differing exchange shapes | NF-44 (unknown position + foreign stop), NF-45 (qty mismatch), NF-39 (unknown position, no stop) | |
| review 1 / Codex: same position, different account | NF-46 | |
| Codex: bare not-found | NF-47 | |
| Codex: generation rollback attack | NF-48 | |
| review 8 / ruling 10: legacy import | NF-49 | REJECT |
| review 3: resting maker fills during HOLD | none | the fake exchange has no resting orders; rows M30/M31 (store writable) and M47-M53 (store unwritable, A24) |

## 9. Changelog

**r4 (2026-10-08, Cairo) - Codex follow-up ruling: the hard-HOLD emergency set:**

- Correction: r3 read A23's "before any other action" literally, so hard HOLD allowed "no exchange action of any kind"
  after the emergency stop (section 3b cross-cutting note). That would leave resting ENTRY/ADD exposure working. Codex
  ruled for the safer reading: it means no ordinary lifecycle action. The note is rewritten.
- New item NC02-A24 "Hard-HOLD emergency set", the only companion exception to A23: (1) narrow query for owned protective
  and risk-adding intents; (2) place/confirm deterministic-client-id reduce-only protection before removing old
  protection; (3) cancel/drain resting ENTRY/ADD intents idempotently; (4) adopt race/partial fills and protect them
  without increasing exposure, staying HOLD; (5) external incident best-effort. Forbidden: cancelling protection because
  the store is unavailable, repricing/fallback of an entry, ordinary management, resuming risk. Every restart repeats
  reconciliation from exchange truth.
- A21 and A23 now reference A24; A20 notes that A24 governs the drain when the store is unwritable; section 1 defines
  **Hard HOLD**. A01-A23 keep their numbers.
- New matrix rows M47-M53 under an unwritable store: cancel succeeds (M47), already gone (M48), timeout/unknown with
  re-query and HOLD (M49), partial fill before the cancel (M50), fill after the cancel (M51), protective order stays
  (M52), restart mid-drain (M53). M43 and M45 updated (M45: "no exchange write" only when no ENTRY/ADD rests).
  M01-M46 keep their numbers.
- Section 5b NF-35 note updated: the drain set is empty for NF-35 (no resting ENTRY/ADD), so its assertion is unchanged.
  NF-35's frozen files and MANIFEST are unchanged. The fixture runner (5.1b) also allows the A24 set; the matrix runner
  (5.2) covers M01-M53. Open question 4 records the follow-up ruling.

**r3 (2026-10-08, Cairo) - Codex ruling on open question 4 (NF-35):**

- New item NC02-A23 "Protection outranks persistence": with the durable journal unavailable, a deterministic-client-id,
  reduce-only protective stop may be placed or repaired (never removing confirmed protection before the replacement is
  confirmed, never increasing exposure), followed at once by hard HOLD, an external incident best-effort, and exchange
  adoption/reconciliation before any other action. Entries, adds and ordinary management get no exception.
- A21 now names A23 as its only exception. A01-A22 keep their numbers.
- New matrix rows M43 (unprotected owned position: emergency stop + hard HOLD + incident), M44 (entry/add wanted:
  refused), M45 (protection already confirmed: no change, HOLD), M46 (replacement: new stop first, old cancelled only
  after the replacement is confirmed). M01-M42 keep their numbers.
- Open question 4 marked resolved with the ruling quoted.
- New section 5b records the NF-35 correction (required behaviour is now emergency protection + hard HOLD; for NF-35's
  own exchange, which already holds a confirmed stop, that means no exchange write). NF-35's frozen files and MANIFEST
  are unchanged. The fixture runner (5.1b) now also allows the A23 emergency stop as an exchange write; the matrix
  runner (5.2) covers M01-M46.

**r2 (2026-10-08, Cairo) - Cowork cross-check + Codex rulings 1-8:**

- Ruling 1 / comment 1 (identity before auto-promotion): A08 and A11 forbid auto-`match` on an identity mismatch or a
  trivially-empty candidate; position equality never proves identity. New rows M40 (NF-46) and M39 (NF-43).
- Ruling 2 / comment 2: "Match" is defined in section 1 (binding, fresh snapshot age, aggregate symbol/side quantity within
  step tolerance, owned order/stop ids and lifecycle, atomic re-check at promotion). A08 uses it.
- Ruling 3 / comment 3: HOLD now drains non-protective intents and adopts and protects late fills (section 1 outcome, new
  A20, rows M30/M31). "Nothing cancelled" is gone.
- Ruling 4 / comment 4: reader and installer rules are separated. M06/M07 moved to section 4 as I01/I02 (their r1 rule-2
  citation was wrong; they are the installer analogue of rule 1); NF-18/19 removed from A01; A03 limited to NEWCORE
  readers; NF-20 moved to I03; the drill uses a simulated pre-NEWCORE reader.
- Ruling 5 / comment 5: evidence goes into an encrypted, access-restricted envelope; only hash and metadata are exposed
  (A04, A16, section 1). A15 copies the torn tail before truncating.
- Ruling 6 / comment 6: M23 rewritten - exchange down forbids promotion and reconciliation claims, not local append-only
  journaling; it is now rule 5 in the first-match list (A12).
- Ruling 7 / comment 7: M10 split into M10 (non-empty, diff explained by final records: MANAGE after applying results) and
  M10b (unexplained diff or empty portfolio with any diff: HOLD). A06 says it explicitly.
- Ruling 8 / comment 8: legacy import is an explicit REJECT (rule 0, A14 rewritten, M26/M27); M27 no longer cites NF-20;
  damaged-primary + future-backup row M29; differing exchange shapes (NF-39, 44, 45); the runner sets mtimes (5.1);
  positive controls are in their own column and section 5a; A01/A02 overlap removed (A02 is now "never clobbered").
- Added rows/fixtures for generation rollback (M42, NF-48) and same-position/different-account (M40, NF-46).
- New items A17-A22 (strict parsing, writer validation, results from evidence, HOLD draining, store I/O faults, foreign
  orders). A01-A16 keep their numbers; M01-M27 keep theirs (M06/M07 retired, not reused); new rows M28-M42 and M10b.
- Rules: rule 0 (REJECT legacy) and rule 5 (exchange down) inserted; old rules 5/6/7 are now 6/7/8.
- New fixtures NF-22..NF-49 (28; 2 positive controls) and requirement ids R-STRICT-PARSE, R-VERSION-TYPE,
  R-WRITE-VALIDATES, R-INTENT-SHAPE, R-NOTFOUND-NOT-PROOF, R-RESULT-BEFORE-APPLY, R-UNCOMMITTED, R-IO-FAULT,
  R-KNOWN-EMPTY, R-IDENTITY, R-MATCH, R-NO-AUTO-CANCEL-FOREIGN, R-ROLLBACK-DETECT, R-HOLD, R-NO-LEGACY-IMPORT.
  NF-01..21 are byte-identical; MANIFEST only gained keys.
- NF-16: `input/.gitkeep` added so git keeps the empty directory; the runner copies only `input_files`.
- Note added: REC/ORD mapping starts at NC-01 (unknown ownership and result evidence are domain types).

**r1 (2026-10-08):** first draft with NF-01..21.
