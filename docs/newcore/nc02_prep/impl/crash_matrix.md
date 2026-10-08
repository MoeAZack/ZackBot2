# NC-02 crash matrix (PRE-STAGE, Claude Code, 2026-10-08 Cairo)

Companion to `nc02_design.md` (section 4 write protocols, section 10 harness). One row per write boundary. "Before" and
"After" describe the bytes on disk (after the power-loss model of design 10.2 has been applied). "Required" is what
recovery must produce on the next start, using the rule numbers of acceptance draft section 3a. "Rows" are matrix rows
M01..M53; "NF" are the frozen fixtures that pin the same boundary in legacy terms. A row marked **GAP** has no matrix row
or no fixture yet; section 3 lists them with a proposal.

Notation: G = current committed generation (named by HEAD), G' = the generation being written, `seg` = the active
segment of G, `=state` / `diff` / `flat` / `down` = exchange states as in 3a. Invariants checked at **every** point
(design 10.3): never MANAGE with an empty portfolio while the exchange differs; never MANAGE on an identity mismatch;
nothing in `data\` changes after ABORT-RO; no exchange write outside the resulting state's permitted set; evidence
originals byte-identical; replay deterministic (same canonical bytes twice).

## 1. Store write boundaries

### 1.1 Event append (design 4.2): intent before send, result before apply

| Point | Crash at | Before | After (disk) | Required on restart | Rows | NF |
|---|---|---|---|---|---|---|
| C-E1 | after E0 validation, before E1 | G, seg ends at lsn n | unchanged | as before the crash; the intent never existed, nothing was sent; =state -> MANAGE (6) | M09 | - |
| C-E2 | during E1 (partial frame) | as C-E1 | seg has a torn tail | tail copied to the envelope, segment sealed (D3), then the rules: the intent was never durable so never sent; =state -> MANAGE (6) | M24 | **GAP** (no NEWCORE log fixture; NF-37 is the snapshot analogue) |
| C-E3 | after E1, before E2 returns | as C-E1 | record complete, torn or absent (power-loss model) | absent/torn: as C-E2. Complete: same as C-E4 (recovery cannot know whether the send happened) | M24, M41 | - |
| C-E4 | after E2, before the send | durable `IntentRecorded`, no send | intent durable, exchange unchanged | intent result UNKNOWN -> query by deterministic client id -> bare not-found proves nothing (A19): HOLD for that symbol/side until NC-01 `not_found_corroborated` (position unchanged across >= 2 reads past the window), then the intent resolves as not executed and rule 6 re-matches | M41 | NF-47 (bare not-found) ; **GAP** row for "durable, never sent" (proposed M57) |
| C-E5 | after the send, before the answer is journaled | intent durable, order live or filled on the exchange | intent durable, no result | query by client id: FINAL record -> `ResultObserved` (E0-E3) -> apply -> re-match -> MANAGE (6). Bare not-found -> HOLD for the symbol/side (A19) | M10, M41 | NF-47 |
| C-E6 | answer received, before `ResultObserved` E1 | as C-E5 | as C-E5 | as C-E5 (the answer is re-queried, never guessed from a position delta) | M10, M41 | NF-45 (delta without a result: HOLD) |
| C-E7 | during the `ResultObserved` write | as C-E5 | torn tail after the intent | tail copied + sealed; result not durable so never applied; re-query as C-E5 | M24, M10 | - |
| C-E8 | after `ResultObserved` E2, before the in-memory apply | result durable | result durable | replay applies it deterministically; =state -> MANAGE | M10 | NF-36 (legacy lost exactly this: the result lived only in an uncommitted temp) |

### 1.2 Generation commit (design 4.1): compaction, HOLD snapshot, promotion, migration, INIT

| Point | Crash at | Before | After (disk) | Required on restart | Rows | NF |
|---|---|---|---|---|---|---|
| C-S1 | during S2 (snapshot body) | HEAD -> G | torn orphan `g<G'>.snap` | orphan never read as state, reported, kept (R-UNCOMMITTED); HEAD -> G + events; =state -> MANAGE (6) | M35 (torn) | NF-37 (positive control) |
| C-S2 | after S2 F, before S3 D | as C-S1 | complete orphan (ntfs model) or no entry (posix model) | as C-S1; a complete orphan is still not state. If G + durable events do not match the exchange -> HOLD (7) | M35 (complete) | NF-36 |
| C-S3 | after S3, before S4 | as C-S1 | complete orphan snapshot | as C-S2 | M35 | NF-36 |
| C-S4 | during S4 (new segment header) | as C-S1 | orphan snapshot + torn orphan segment | as C-S2; both orphans kept | M35 | - |
| C-S5 | after S5, before S6 | as C-S1 | orphan snapshot + orphan segment | as C-S2; the next commit uses G'+1 (names are never reused) | M35 | NF-36 |
| C-S6 | during S6 (HEAD slot write) | HEAD.x -> G valid | HEAD.y torn, HEAD.x -> G | the torn slot is the expected crash state, not damage: use HEAD.x -> G, report orphans, continue as C-S2 | **GAP** (proposed M54) | - |
| C-S7 | after S6 F, before S7 | HEAD -> G' | HEAD -> G', anchor -> G | max(HEAD, anchor) = G'; normal rules on G'; the anchor is refreshed at the next commit | **GAP** (proposed M55, anchor lag) | - |
| C-S8 | during S7 (anchor slot) | as C-S7 | one anchor slot torn | the other slot is used; as C-S7 | M55 (proposed) | - |
| C-S9 | after S7 | HEAD -> G', anchor -> G' | complete | normal rules on G' | M09 | - |
| C-S10 | during retention GC (S8) | G-2 retained | G-2 files partly deleted | GC must first journal `GcIntent{files}` and commit a HEAD that drops G-2, then delete; files listed by a durable `GcIntent` are "retired", not orphans; without that record, leftover files are orphans (kept, reported) | **GAP** (decision D16) | - |

A promotion (design 4.4 R5) is a generation commit with `trust: MANAGED`: a crash at C-S1..C-S6 leaves the account in
HOLD with a stale reconciliation record (A08 runs again with a fresh snapshot); only after C-S6 completes is it MANAGE.
A HOLD snapshot (damage path) is a generation commit with `trust: HOLD`: a crash at C-S1..C-S6 leaves the damaged G as
head, so recovery reaches the same HOLD again (A05).

### 1.3 Evidence copy (design 4.3)

| Point | Crash at | Before | After (disk) | Required on restart | Rows | NF |
|---|---|---|---|---|---|---|
| C-V1 | during V2 (blob write) | damaged member M, no blob | torn, un-indexed `.zbe` | M still damaged -> HOLD (4); V3 fails on the torn blob -> the store deletes its own unindexed blob (D4) -> copies again; M byte-identical throughout | M36, A04 | NF-34 (the ENOSPC variant: no partial left) ; **GAP** decision D4 |
| C-V2 | after V2 F/D, before V3 | as C-V1 | complete un-indexed blob | re-verify (decrypt + sha256) then index; never trusted unverified | M36 | - |
| C-V3 | after V3, before V4 | as C-V1 | verified, un-indexed blob | as C-V2 | M36 | - |
| C-V4 | during V4 (index append) | as C-V1 | `index.seg` torn tail | index tail copied + sealed like a log tail; blob re-verified and indexed | M24, M36 | - |
| C-V5 | after V4, before V5 (`EvidenceLinked`) | as C-V1 | indexed, not linked from the account log | HOLD (M still damaged); link appended idempotently (key: sha256 + incident) | M36 | NF-38 (legacy: evidence present but unlinked) |
| C-V6 | after V5 | linked | complete | HOLD with the incident linked; candidate offered (A07) | M13, M36 | NF-08, NF-09, NF-38 |
| C-V* under ENOSPC/EROFS | first failing W/F | as C-V1 | own partial deleted at once; nothing else written | hard HOLD (A21), original untouched, failure in `incidents\boot.jsonl` (out-of-band) | M34 | NF-34, NF-35 |

### 1.4 Torn-tail seal (design 3.4, decision D3)

| Point | Crash at | Before | After (disk) | Required on restart | Rows | NF |
|---|---|---|---|---|---|---|
| C-T1 | after the tail is in the envelope, before the sealing HEAD commit | seg has a torn tail | tail still in place, evidence indexed | tail detected again; evidence already present (idempotent by sha256); seal commit; then the rules | M24 | **GAP** (no NEWCORE log fixture) |
| C-T2 | during the sealing HEAD write | as C-T1 | one HEAD slot torn | as C-S6 then C-T1 | M24, M54 (proposed) | - |
| C-T3 | after the seal, before the first append to the new segment | sealed | new empty segment | normal rules | M24 | - |

If Codex keeps A15's "truncate" instead of D3, add C-T1' (after the copy, before the truncate's F: the truncate may or
may not be durable; both states must recover to the same result) and C-T2' (truncate durable, HEAD not updated).

### 1.5 Reconciliation and promotion (design 4.4)

| Point | Crash at | Before | After (disk) | Required on restart | Rows | NF |
|---|---|---|---|---|---|---|
| C-R1 | after the exchange snapshot (R1), before R3 | HOLD | unchanged | HOLD; A08 runs again | M13, M14 | NF-08, NF-11 |
| C-R2 | during R3 | HOLD | torn recon record | tail copied + sealed; HOLD; A08 again | M24 | - |
| C-R3 | after R3, before R4/R5 | HOLD + durable recon record | recon record, no promotion | HOLD; the record is older than `MATCH_MAX_AGE_MS`, so R1-R5 run again with a fresh snapshot (no half-promotion) | M11, M13 | NF-08, NF-09 |
| C-R4 | during the promotion commit | as C-R3 | as C-S1..C-S6 | HOLD (MANAGED exists only in a committed snapshot) | M11, M13 | - |
| C-R5 | after the promotion HEAD, before the anchor | MANAGED G' | HEAD -> G', anchor -> G | as C-S7; rule 6 re-matches a fresh snapshot -> MANAGE | M09 | - |

### 1.6 HOLD entry (design 6, P5/P8)

| Point | Crash at | Before | After (disk) | Required on restart | Rows | NF |
|---|---|---|---|---|---|---|
| C-H1 | HOLD decided, before `HoldEntered` E1 | cause present (damage/diff/identity/unknown result) | unchanged | the same cause is re-derived -> HOLD (A05). A transient cause that is gone on restart is legitimately re-evaluated by rule 6 (a match needs MANAGED trust + recon + a fresh snapshot) | M17 | NF-07, NF-10, NF-26 |
| C-H2 | during `HoldEntered` | as C-H1 | torn tail | tail sealed; as C-H1 | M24 | - |
| C-H3 | during a HOLD snapshot commit | damaged head | as C-S1..C-S6 | damaged G is still head -> HOLD again (A05) | M16, M17 | NF-10, NF-26 |
| C-H4 | after `HoldEntered`, then any number of saves | HOLD durable | HOLD snapshot(s) | still HOLD; a save in HOLD writes `trust: HOLD` only (A05) | M17 | NF-07, NF-10, NF-26 |

### 1.7 Migration (design 4.5)

| Point | Crash at | Before | After (disk) | Required on restart | Rows | NF |
|---|---|---|---|---|---|---|
| C-M1 | during the migrated snapshot | HEAD -> G (format F<R) | torn orphan G' | orphan kept; G read with the F codec; migration re-runs into G'+1 | M11, M12 | - |
| C-M2 | after it, before HEAD | as C-M1 | complete orphan G' | as C-M1 | M11 | - |
| C-M3 | after HEAD | HEAD -> G' (R, trust HOLD, provenance migration) | G retained | HOLD -> A08 -> MANAGE (M11) or HOLD with items (M12); G stays readable by its own build until installer acceptance + recon (A13) | M11, M12, I04 | NF-21 (legacy v0->v1 positive control) |
| C-M4 | after HEAD, before the anchor | as C-M3 | anchor -> G | as C-S7 | M11 | - |

### 1.8 INIT (design 4.6)

| Point | Crash at | Before | After (disk) | Required on restart | Rows | NF |
|---|---|---|---|---|---|---|
| C-I0 | exchange down at I0 | nothing | nothing (no dir, no anchor) | INIT waits, zero writes (rule 5) | M23 | - |
| C-I1 | after the mkdirs, before the G1 snapshot | nothing | empty account dir (evidence DACL set or not) | interrupted INIT (D9): re-INIT; flat -> MANAGE known-empty; non-flat -> HOLD-INIT; DACL re-applied before use | M21, M22, M37 | NF-16, NF-39 |
| C-I2 | during the G1 snapshot | as C-I1 | torn orphan G1 | interrupted INIT -> re-INIT with G2; orphan kept | M37 | NF-39 |
| C-I3 | after G1 (init provenance), before HEAD | as C-I1 | complete init G1, no HEAD | interrupted INIT (only an init G1 and no HEAD): re-INIT. Anything else without a HEAD (G > 1, non-init provenance) is damage -> HOLD (4) | M37 | NF-39 |
| C-I4 | after HEAD, before `anchors\by-binding` | HEAD -> G1 | no by-binding entry | account found by scanning `accounts\*\HEAD.*` read-only; entry written at the next commit | M21 | - |
| C-I5 | complete | - | - | flat: MANAGE known-empty; non-flat: HOLD-INIT; confirming the account alone never clears HOLD-INIT | M21, M22, M37 | NF-16, NF-17, NF-39, NF-49 |

### 1.9 Owner actions (HOLD / HOLD-INIT items)

| Point | Crash at | Before | After (disk) | Required on restart | Rows | NF |
|---|---|---|---|---|---|---|
| C-O1 | `OwnerAction` journaled, before the exchange action | HOLD item open | owner intent durable | as C-E4: query by deterministic id; never re-sent blindly | M41 | - |
| C-O2 | after the exchange action, before its result | as C-O1 | as C-O1 | as C-E5 | M10 | - |

## 2. Exchange-side boundaries in hard HOLD (no store writes possible)

Nothing is journaled in hard HOLD, so every restart repeats the A24 set from exchange truth (M53). Deterministic client
ids make each step idempotent. With a writable store on restart, the hard-HOLD anchor marker (D11) or the exchange
difference forces HOLD -> A08.

| Point | Crash at | Exchange before | Exchange after | Required on restart | Rows | NF |
|---|---|---|---|---|---|---|
| C-X1 | emergency stop sent, not yet confirmed | owned position, no confirmed stop | stop maybe placed | narrow query by the emergency id: found -> confirmed, not duplicated; absent -> placed again (same id) | M43, M53 | - (NF-35's stop already exists: M45) |
| C-X2 | replacement confirmed, old stop not yet cancelled | old stop no longer covers | old + new stop | both reduce-only (exposure never increases); old cancelled now | M46, M52, M53 | - |
| C-X3 | drain cancel sent, answer lost | resting ENTRY/ADD | cancelled, filled or working | re-query by id; re-cancel only while working; never re-sent as an entry | M47, M48, M49, M53 | **GAP** (fake exchange has no resting orders) |
| C-X4 | partial/race fill seen, adoption stop not yet placed | fill on the exchange | unprotected adopted quantity | adopt from exchange truth, protect exactly that quantity reduce-only (new stop confirmed before any old one is cancelled) | M50, M51, M53 | **GAP** (as C-X3) |
| C-X5 | external incident write | - | best-effort, may be missing | repeated on restart; never a durable result (A24) | M43-M53 | NF-35 (incident expected) |

## 3. Gaps and proposals (for Codex; the acceptance draft and the fixtures are not edited)

1. **No NEWCORE event-log fixture.** Legacy had no log, so M24 (torn tail) and M25 (mid-segment damage), C-E2/E7, C-T*
   and C-V4 are matrix-runner/crash-harness cases only. Proposal: the crash harness generates them; no frozen NF needed.
2. **Commit-record points are not in the matrix.** Proposed new rows (numbers after M53): **M54** one HEAD slot torn ->
   use the other slot (not damage); **M55** anchor behind HEAD -> normal, anchor ahead of HEAD -> rule 2; **M56** both
   HEAD slots invalid while snapshots exist -> HOLD (4).
3. **"Durable intent, never sent" (C-E3/C-E4)** is the most common crash and has no row of its own. Proposed **M57**:
   durable intent, exchange unchanged -> symbol/side HOLD until `not_found_corroborated`, then resolved without an owner
   action (NC-01 `Evidence.NOT_FOUND_CORROBORATED`). Needs the corroboration window as an NC-02 parameter (NC-01/NC-03
   own its value).
4. **Partial evidence (C-V1)** conflicts with R-UNCOMMITTED ("never silently deleted") versus A04 ("no partial evidence
   is left"): decision D4.
5. **Retention GC (C-S10)** has no row and no rule: decision D16 (journal `GcIntent` before deleting retired files).
6. **Resting orders.** C-X3/C-X4 (and M30/M31, M47-M53) need `open_orders` in the fake exchange; no NF fixture can carry
   them. The runner's `FakeExchange` already accepts `open_orders`; the matrix runner supplies them.
7. **Migration crashes** are covered only by the installer drill (section 4 of the draft); C-M1..C-M4 should also run in
   the crash harness with a synthetic F<R store.
8. **Inferred NTFS behaviour.** C-S2/C-S3 under the `ntfs` model assume a created file survives without a directory
   flush, and C-E2 assumes NUL zero-fill can appear after a crash. The real-kill drill (design 10.5) measures process
   kill only; true power loss needs a VM hard reset (Hyper-V `Stop-VM -TurnOff`). Proposal: Cowork runs that drill once
   on the target Windows build and records the results as measured.
9. **ABORT-RO has no crash points in `data\`** by construction (zero writes). The only write is the best-effort boot
   incident outside `data\`; a crash there loses one log line and the next start writes it again.
10. **NF-16 with a surviving anchor** gives rule-2 HOLD rather than HOLD-INIT (decision D2). Both block new risk; the
    expected-outcome table in the runner accepts only HOLD-INIT today and would need the same decision.

## 4. Fixture coverage of crash boundaries (legacy D01-D04 -> NEWCORE points)

| Fixture | Legacy boundary (Cowork D-id) | NEWCORE points | Required outcome |
|---|---|---|---|
| NF-36 | D01 crash after temp fsync, before replace (complete orphan) | C-S2, C-S3, C-S5, C-E8 | HOLD (7): orphan never read; G + durable events vs flat exchange is an unexplained diff |
| NF-37 | D02 crash mid temp write (torn orphan), positive control | C-S1 | MANAGE (6); orphan kept and reported |
| NF-38 | D03 crash after move-aside, before the replacement save | C-V5, C-V6 (copy-not-move makes "G missing" external, not self-inflicted) | HOLD, evidence linked, candidate G-1 (M36) |
| NF-39 | D04 interrupted first run | C-I1, C-I2, C-I3 | HOLD-INIT (non-flat); confirm alone never clears it (M37) |
| NF-34 | ENOSPC on every write | C-V* under ENOSPC, first E1/S2 | hard HOLD, no partial evidence, out-of-band report |
| NF-35 | EROFS on every write | first E1/S2 | hard HOLD, no exchange write (stop already confirmed), incident (M45) |
| NF-07, NF-10, NF-26 | failed-closed not durable across saves/restart | C-H1, C-H3, C-H4 | same outcome after restart (ABORT-RO for NF-07, HOLD for NF-10/26) |
| NF-47 | bare not-found after a close intent | C-E4, C-E5 | symbol/side HOLD, result UNKNOWN (M41) |
| NF-48 | whole-store rollback | C-S7 inverse (anchor ahead) | HOLD (rule 2 with an anchor, rule 7 via the diff without one) |
