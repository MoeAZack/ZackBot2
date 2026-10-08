# NC-02 recovery/state acceptance - DRAFT (REC-01 retargeted)

Status: draft for Codex (Integration). Prepared 2026-10-08 (Cairo) by Claude Code, read-only, on master `cff3f88`.
Owner decision (#13): the legacy bot gets no repair. REC-01's direction moves into NEWCORE **NC-02 (state/event store)**.
The legacy behaviour is kept only as **frozen negative fixtures** (`fixtures/NF-01..21`). This draft proposes no change to legacy code.

Direction carried over from REC-01 (Codex, #13 / PR #34):

- **D1:** a future schema aborts start-up and leaves state and backup byte-identical.
- **D2:** damage to a current-schema file fails closed and keeps the evidence.
- **D3:** an account is never left as an empty managed account.
- **D4:** a rollback/reconciliation matrix is defined, and explicit exchange/state reconciliation comes before management.

## 1. Terms used by the contract

| Term | Meaning |
|---|---|
| Store unit | Everything NC-02 persists for one account: the marker (account identity), the snapshot generations, the append-only intent/result event log and the reconciliation records. It is versioned and validated as **one** unit (legacy `install.json` drifted independently of `state.json`; see NF-05). |
| Header | Every snapshot and every event segment carries `account_key`, `generation` (monotonic), `writer_build`, `format_version` and `min_reader_version`. A store-level `high_water` holds the highest generation ever committed and the highest `writer_build`. |
| Reader R | The running build. `F` is the on-disk format. |
| Reconciliation record | A durable record that binds generation G to an exchange snapshot (positions, open orders, protective orders, with their ids). Its verdict is `match` or `owner-resolved` (each difference adopted, closed or cancelled by an explicit owner action, and each one recorded). |
| Outcomes | **ABORT-RO**: refuse to start this account; no byte of the store is written (no rename, no rotation, no "persist the pause"); report through the exit code and a boot incident log *outside* the store. **HOLD**: the account is loaded read-only into a quarantine view and no AccountContext is created, so nothing is ordered, cancelled, repaired or booked. Existing exchange protective orders are left alone. Only the reconciliation flow can leave HOLD. **MANAGE**: normal operation. **INIT**: first-run initialization. |

## 2. Acceptance list

Each item is a test. Each NF fixture pins the legacy failure the item rejects.

| ID | Requirement | Pinned by |
|---|---|---|
| NC02-A01 | **Future format means ABORT-RO.** If any member of the store unit has `min_reader_version > R`, or a `writer_build` newer than R with an unknown format, start-up aborts before any write. The sha256 of every file in the data folder is identical before and after, and so are the directory listing and mtimes. Retrying start-up gives the same result. | NF-01, 02, 03, 04, 05, 06, 19 |
| NC02-A02 | **A future backup alone also aborts.** A newer generation anywhere in the unit (an older-format primary with a future-format backup or event segment) means ABORT-RO. It is never overwritten by the next save. | NF-06 |
| NC02-A03 | **Generations are monotonic (rollback detection).** A reader that finds `high_water.generation` greater than the snapshot it can read, or `high_water.writer_build` newer than itself, aborts read-only. A store last written by an older build after a newer one is detected through the `writer_build` history and goes to HOLD. | NF-18, 19 |
| NC02-A04 | **Evidence is copied, not moved.** Damaged bytes are preserved bit-for-bit through copy, fsync and mark. The original path is never renamed away and then refilled by a rewrite. The evidence is referenced from a durable incident record. | NF-08, 09, 10, 12, 20 |
| NC02-A05 | **Fail-closed is durable.** HOLD survives any number of restarts and saves until a reconciliation record exists. A save made while in HOLD cannot produce a "clean" store that the next start trusts. | NF-07, 10 |
| NC02-A06 | **No empty managed account.** An initialized account (marker, any generation, evidence or reconciliation history) never loads as an empty portfolio. "Empty" is legal only on INIT with a flat exchange, or after an owner adoption record. | NF-02, 07, 10, 11, 13, 14, 15, 16, 17 |
| NC02-A07 | **A backup is a candidate, never managed state.** An older generation or a replay of the event log is offered as a reconciliation candidate. It becomes managed only after A08 passes. Restoring it never books an exit, P&L or orders. | NF-08, 09, 11 |
| NC02-A08 | **The reconciliation gate.** Leaving HOLD requires a reconciliation record for the generation being promoted. A matching exchange snapshot gives automatic `match`. Any difference (extra or missing positions, unknown or missing orders, a protective order without a lot) needs a per-item owner action. Resuming entries, toggling pause or confirming the account does **not** clear HOLD. MANUAL and Telegram entry paths go through the same gate. | NF-02, 08, 10, 11, 13, 14 (legacy: the resume gate checks only `install_block`, app.py:1225 and telegram_ctl.py:524; manual entries bypass pause at engine.py:3415) |
| NC02-A09 | **First run against a non-flat exchange is blocked.** INIT needs a successful exchange snapshot. If it shows positions or open orders, the account is bound only as HOLD-INIT until each item is adopted or closed by the owner. A new or emptied data folder never yields "entries open". | NF-16, 17 |
| NC02-A10 | **Unreadable is not damaged.** A persistent I/O failure (sharing violation, permission) leaves the file untouched and never saved over, and the account stays in HOLD. Legacy got the "untouched" part right; NC-02 must also stop it from becoming an empty account. | NF-13 |
| NC02-A11 | **The store unit is one source of truth.** If the marker and the snapshots disagree (missing, invalid, other account), the result is HOLD. A backup of the marker can never silently clear an owner-confirmation requirement. | NF-05, 15 |
| NC02-A12 | **Exchange unreachable.** No transition out of HOLD and no INIT happen while the exchange snapshot cannot be taken. MANAGE continues to protect only when it already was MANAGE (NC-03/NC-04 own that). | matrix rows X* |
| NC02-A13 | **Migrations are additive and generational.** Migrating F older to R writes a **new** generation alongside, never in place. The prior generation stays readable by its own build until R is confirmed (installer acceptance) and a reconciliation record exists for the migrated generation. A down-migration exists only as an explicit owner tool. | NF-21 (positive control), installer rollback (see 4) |
| NC02-A14 | **Legacy import is one way.** NEWCORE reads legacy `state.json`/`settings.json`/`install.json` **read-only**, records their sha256 in the import record and lands in HOLD (A08) before managing. It never rewrites legacy files. A legacy relaunch after import is out of scope (legacy is disposable). The installer/launcher must refuse a rollback across the NEWCORE boundary unless the owner explicitly asks for it. | NF-18, 20, 21 |
| NC02-A15 | **A torn tail is not damage.** An incomplete final event record (crash mid-append) truncates to the last complete record and is reported. Damage before the tail is A04/A05 damage. The crash matrix covers every write boundary (as in the plan's C2). | crash matrix |
| NC02-A16 | **No secrets anywhere in the store, its evidence or its incident log.** This carries over AUD-05 r2-r5. | existing AUD-05 tests as the spec source |

## 3. Rollback/reconciliation matrix

The rules are applied in order, and the first match wins. R is the reader build. For F: "F<R" means migratable, "F=R" means current and "F>R" means future (`min_reader_version > R`). The exchange states are **flat**, **=state** (matches the newest trusted candidate), **diff** (any position or order difference) and **down** (no snapshot possible).

### 3a. Rules

1. Any member of the unit has F>R, or a newer `writer_build` with an unknown format (in the primary, backup or event segment): **ABORT-RO**, whatever the damage or exchange state.
2. `high_water.generation` is greater than the best readable generation, or the history shows an older writer after a newer one: **ABORT-RO** if the newest writer is newer than R, otherwise **HOLD**.
3. Any member is unreadable (I/O): **HOLD**, with no writes to that member.
4. Any damage, a missing member with the marker or evidence present, or a marker/snapshot mismatch: **HOLD**, with evidence copied (A04) and the best intact generation offered as a candidate (A07).
5. The store is intact, F=R or F<R, and its last reconciliation record covers the current generation: **MANAGE** (F<R migrates first as a new generation, A13).
6. The store is intact but there is no reconciliation record for the current generation (first start after a migration or import, or after HOLD): **HOLD** until A08.
7. Nothing at all exists (no marker, generation, evidence or legacy files): **INIT**. A flat exchange gives MANAGE with an empty portfolio. A diff exchange gives **HOLD-INIT** until each item is adopted or closed. A down exchange means INIT waits and nothing is written.

From HOLD: if the exchange matches the candidate, a reconciliation record (`match`) is written automatically and the account goes to MANAGE. Owner-gated alternative: the owner may require a confirmation even on a match. **Codex to decide.** On diff, the account stays in HOLD with a per-item list. On down, it stays in HOLD.

### 3b. Representative cells (one row per test)

| # | Reader | F | Damage | Exchange | Expected | Legacy observed (fixture) |
|---|---|---|---|---|---|---|
| M01 | N | F>R primary only | intact | any | ABORT-RO, bytes identical | moved aside, older .bak managed, .bak overwritten after 2 saves (NF-01) |
| M02 | N | F>R primary + backup | intact | diff | ABORT-RO | both moved aside, 0 lots, entry sent (NF-02) |
| M03 | N | F>R backup only | intact | =state | ABORT-RO | silent, .bak overwritten (NF-06) |
| M04 | N | F>R settings | intact | any | ABORT-RO | rewritten from .bak or from defaults (NF-03/04) |
| M05 | N | F>R marker | intact | any | ABORT-RO | moved aside, then silently restored from .bak on the next restart (NF-05) |
| M06 | N-1 | F=N (newer than reader) | intact | =state | ABORT-RO (rule 2 via min_reader_version) | OLD manages and rewrites it (NF-18) |
| M07 | N-1 | F=N+1 | intact | =state | ABORT-RO | OLD launders the v2 label (NF-19) |
| M08 | N | F=R, older writer ran after a newer one | intact | =state | HOLD then A08 | undetectable (NF-18 d3) |
| M09 | N | F=R | intact, reconciled | =state | MANAGE | MANAGE (OK) |
| M10 | N | F=R | intact, reconciled | diff | MANAGE plus the normal NC-03 untracked/repair flow (not an NC-02 HOLD) | untracked after 2 passes |
| M11 | N | F<R | intact | =state | migrate as a new generation, then HOLD, then auto `match`, then MANAGE | in-place v0 to v1 (NF-21, OK with confirm) |
| M12 | N | F<R | intact | diff | migrate as a new generation, then HOLD with a per-item list | install_mismatch only when the state owns something |
| M13 | N | F=R | primary damaged, backup intact | =candidate | HOLD, evidence copied, auto `match`, MANAGE | backup promoted directly; resume allowed with no check (NF-08/09) |
| M14 | N | F=R | primary damaged, backup intact but older | diff | HOLD; candidate shown with its diff | backup promoted, entry sent (NF-11) |
| M15 | N | F=R | both damaged | flat | HOLD; the owner may adopt "empty" explicitly | defaults or empty, resume allowed |
| M16 | N | F=R | both damaged | diff | HOLD (never an empty managed account) | 0 lots, entry sent (NF-10) |
| M17 | N | F=R | both damaged, then save and restart | diff | still HOLD (A05) | clean empty start (NF-07) |
| M18 | N | F=R | primary unreadable (I/O) | diff | HOLD, file untouched | untouched, but 0 lots and resume allowed (NF-13) |
| M19 | N | F=R | snapshot missing, marker present | diff | HOLD | failed_closed, but resume allowed, entry sent (NF-14) |
| M20 | N | F=R | marker missing, snapshot present | any | HOLD (A11) | install_block (OK, NF-15) |
| M21 | N | none | nothing exists | flat | INIT then MANAGE (empty) | INIT (OK) |
| M22 | N | none | nothing exists | diff | HOLD-INIT | INIT, entries open, entry sent (NF-16/17) |
| M23 | N | any | any | down | no transition out of HOLD, no INIT, no store writes | n/a |
| M24 | N | F=R | event log torn tail | =state | truncate to the last record, report, then the rules above | n/a (new in NC-02) |
| M25 | N | F=R | event log mid-segment damage | any | HOLD, evidence copied | n/a |
| M26 | N (legacy import) | legacy v0/v1 files | intact | =state | read-only import, HOLD, auto `match`, MANAGE; legacy bytes unchanged | n/a |
| M27 | N (legacy import) | legacy | damaged | any | HOLD, legacy bytes unchanged, evidence recorded | legacy: empty and open (NF-20) |

**Cross-cutting for every HOLD row:** MANUAL, Telegram and UI entries are refused, and resuming entries does not clear HOLD. Legacy breaks both: the manual path skips the pause (engine.py:3415), and resume consults only `install_block`.

## 4. Installer/rollback interaction

- The legacy installer rollback (installer.ps1 `Invoke-Rollback`) restores the **exe only**. A new build that migrated the data in place before failing its launch leaves the old exe reading newer files (NF-18/19). NC-02 therefore requires A13: generational writes and no in-place migration before installer acceptance.
- A rollback drill test is required. Build N writes generation G+1, and installer rollback restores N-1. N-1 must ABORT-RO under rule 2 (or read its own generation G and enter HOLD, if the owner chose "keep N-1 generation"). It never manages G silently.

## 5. Regression tests to land with NC-02

1. **Fixture pack runner.** For each `fixtures/NF-*`, it copies `input/` into a temp folder and loads a fake exchange from `exchange`. It then starts the NC-02 store and asserts the `nc02_required` ids: the hashes of every input file, the outcome (ABORT-RO/HOLD/MANAGE/INIT), the AccountContext not created, no fake-exchange calls other than reads, and HOLD after a restart.
2. **Matrix runner.** It parametrizes M01-M27 from a table (reader × F × damage × exchange) and checks the outcome and the "no write" set.
3. **Gate tests.** In HOLD, every entry source (auto, manual, Telegram, trailing, grid, maker) is refused, and resume/confirm do not clear HOLD (A08).
4. **Crash matrix (from the plan's C2).** Kill at every write boundary of snapshot, event append, reconciliation record, migration and evidence copy. After restart, the outcome follows the matrix, and no state is half-promoted.
5. **Rollback drill (section 4)** with a simulated N-1 reader.
6. **Property test.** For random store units with an injected future or damaged member, the outcome is never MANAGE with an empty portfolio while the exchange is non-flat.

## 6. Reusing the golden/ledger approach

- Use the AUD-07 pack mechanics for the fixtures: a `MANIFEST.json` holding the sha256 of each fixture **contract** (`id`, `reader_build`, `input_files`, `exchange`, `nc02_required`), as already computed in `fixtures/MANIFEST.json`. Add an append-only, hash-chained `CORRECTIONS.json` in which every NC-02 behaviour that differs from `legacy_observed` is an accepted correction entry. That prevents silent rebaselining.
- The `legacy_observed` text is documentation, not an assertion. The legacy engine is not run in NC-02 CI.
- The fixture inputs are generated (`make_fixtures.py`, `fix_nf21.py` for the pre-AUD-05 writer), and then frozen as bytes. Their hashes are the contract, so regenerating them is a ledger event.

## 7. Open questions for Codex

1. Is the auto-`match` exit from HOLD acceptable, or does every HOLD exit need an owner confirmation (the stricter option)?
2. Should the boot incident log outside the store (needed for ABORT-RO with zero store writes) be per account or per install?
3. The scope boundary between NC-02 HOLD and NC-03/NC-04 "untracked" handling while in MANAGE (M10): this draft keeps M10 out of NC-02.
4. Should the legacy settings be imported, or should NEWCORE start from reviewed defaults and record the legacy file's hash only?
