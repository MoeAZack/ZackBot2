# AI-SCAN-04 — producer wiring prerequisites (PAPER / shadow only)

**Status: DRAFT — unsigned.** This is a contract only. It adds no runtime code and grants no authority. The machine
twin `ai_scan_04_contract.v1.json` sits next to this file and is authoritative; the JSON wins any conflict. Tests read
the JSON and check this file against it. Assignment: Codex 6468672436, which set three pre-producer requirements
(a), (b) and (c). Built from `origin/master` @ `50ec4e3`. `builds_on` pins the **accepted merge SHAs**: AI-SCAN-03
(#227) @ `612b537` and AI-SCAN-03A (#229) @ `f756068`. Neither is edited here.

**Integration review 6480873345 is applied.** R1 (blocking) adds a durable active checkpoint (§5). Q1, Q2 and Q3 are
resolved by ruling (§10). The §4 and §5 numbers are **frozen by Integration ruling 6480873345**, not proposals. §6
remains a proposal.

**Integration ruling 6483289354 is applied** (JSON `ruling_6483289354`). C1: a **two-phase intent/commit checkpoint**
(§5). C2: **sealed exposure is published separately** (§4). C3: **startup I/O is bounded** (§4). C4: **checkpoint
persistence failure is fail-closed** (§5).

**Integration ruling 6483743382 is applied** (JSON `ruling_6483743382`). E1: a **seal and rotation crash protocol**
(§5). E2: **orphan checkpoint temp files are never authoritative** (§5). E3: **one row per transaction, frozen** (§5).
E4: **a partial append or ENOSPC is confirmed fail-closed** (§5).

**Integration ruling 6484022181 is applied** (JSON `ruling_6484022181`). R1: **the E1 advance hashes sealed file n and
requires file n+1 absent or empty** (§5). R2: **the forced seal and the startup seal are distinct paths**, and a
forced-seal checkpoint-advance persist failure recovers through E1 (§5). Matching: **E1 uses the committed triple with
pending == null** (§5).

## 1. Scope and authority

- The producer stays **default OFF**. Enabling it needs **a separate Integration ruling** after this contract is
  FROZEN and its implementation slice is accepted. This contract grants nothing.
- **PAPER only.** Live mode is refused. **Authority: none.** No order gating, sizing or PAPER order authority. Trading
  code never reads the output. No settings or engine-state writes. The engine lock is never held.
- **No model or API call, and no network access.**

## 2. Ledger constants it builds on

These come from `ai_scan_ledger.py` @ `f756068`, and they equal the AI-SCAN-03 `rotation_prerequisite.limits`.
`SEG_MIN` = 1 MiB, `SEG_MAX` = 16 MiB, `SEGS_PER_READ` = 4 and `MAX_SEGMENTS` = 4096. That gives a 64 GiB worst-case
sealed corpus.

## 3. Threat model

Every hash is unkeyed: `record_hash`, segment `sha256`, `chain` and `manifest_hash`. They detect accidental corruption,
torn writes and uncoordinated edits. They also detect a stale eligibility generation after the writer stalls. They do
**not** stop a local actor who recomputes all of them together. Keyed authentication is DEFERRED
(`keyed_ledger_authentication`). **Q2 ruling:** unkeyed hashes are accepted for **PAPER only**, against corruption
and uncoordinated edits. This contract claims **no protection against a local actor who can rewrite the chain**.
Keyed authentication is a **mandatory pre-live decision**. Readers do not rely on eligibility: `read_page` re-hashes every sealed segment it
reads. Requirement (a) therefore bounds how long the **producer** can keep appending after an edit. It does not
protect reads, which are already protected.

## 4. (a) Bounded periodic integrity refresh

**Problem.** Eligibility stays True after a sealed-segment edit until the next `refresh()`, and `append()` never
re-verifies sealed files. The 03A `refresh()` reads every sealed byte, up to 64 GiB, after every seal.

**Rule.** The rolling refresh tick runs on the writer thread only.

| Parameter | Value | Why |
|---|---|---|
| `tick_period_s` | 60 | It matches the 60 s C1 heartbeat, so one writer timer drives both. |
| `sealed_segments_per_tick` | 16 | Q3 ruling. Verified **sequentially**, one at a time; never held simultaneously. |
| `max_sealed_bytes_per_tick` | 256 MiB | 16 × 16 MiB `SEG_MAX`. That is about 4.3 MiB/s on average. |
| `max_active_bytes_per_tick` | 16 MiB | The active digest check reads at most `segment_bound`. |
| `max_segment_bytes_per_tick` | 272 MiB | Sealed plus active. The manifest is extra and bounded by 4096 entries. |
| `max_full_sweep_ticks` / `max_full_sweep_s` | 256 / 15360 | ceil(4096 / 16) = 256 ticks; 256 × 60 s = 15,360 s (4 h 16 m) at the cap. At 100 segments it is 7 min. |
| `sealed_max_undetected_s` | 15360 | C2 ruling. One full cursor sweep: the sealed-edit exposure. Distinct from the active 60 s and the 180 s eligibility age. |
| `max_segments_held_in_memory` | 1 | The memory bound is **one segment's streaming buffer at a time** (≤ `SEG_MAX`), never 16 × `SEG_MAX`. |
| `max_bytes_per_seal_refresh` | 16 MiB | A seal verifies only the manifest and the new entry, never the corpus. |
| `eligibility_max_age_s` | 180 | 3 ticks. If the writer stalls or dies, the hook fails closed. |

Each tick does four things. It reloads the manifest and requires that its `manifest_hash` matches the writer's copy. It
verifies the next 16 sealed entries, sequentially, at a rolling cursor that wraps. It checks the active digest (§5). Then it publishes a
generation stamped with `time.monotonic()`. Any mismatch disables the producer. Ticks never overlap; a skipped slot is
counted `refresh_overrun`. The hook reads one cached tuple plus one monotonic clock. A generation older than 180 s is
ineligible (`eligibility_stale`), so G4 stays intact: no disk I/O, hashing or manifest traversal on the trading thread.
A **repeated overrun** that makes eligibility older than 180 s **stays fail-closed**; the caps are never raised to catch
up. **Startup (Q1 ruling):** full verification of the corpus once per start is accepted for PAPER/shadow only because it
runs **exclusively off the trading thread**, the producer is **published ineligible until it completes**, and it
**never blocks engine/PAPER startup**. A bounded/indexed startup is a **pre-live optimization**, not a blocker now.

**Sealed exposure (C2 ruling 6483289354).** `sealed_max_undetected_s` = 15,360 s, the 4 h 16 m full cursor sweep. A
sealed edit is **detected within 15,360 s**; refusal follows on detection, with **at most the 180 s eligibility age on
top** (worst case 15,540 s). The active `max_undetected_exposure_s` = 60 s covers **the active segment only**; it does
not cover sealed files.

**Startup I/O (C3 ruling 6483289354, JSON `refresh_cadence.startup_io`).** Full startup verification stays **off the
trading thread** and the producer is **ineligible** until it completes. Reads are capped at **64 MiB/s** by default
(`read_cap_bytes_per_s` = 67108864), **configurable downward only**. It holds **one segment buffer** and makes a
**cooperative yield between segments**. It never blocks engine or PAPER startup. Worst case at the cap: **64 GiB /
64 MiB/s = 1024 s** (about 17 min).

## 5. (b) Active-segment exposure and fail-closed recovery

**Problem.** The active segment is not hash-bound. A same-size edit to rows already written goes undetected, and
`seal()` would then bind the edited bytes into the manifest.

**Rule.** The writer keeps an in-memory **running digest**: sha256 state plus length, over exactly the bytes it
appended. Every tick re-reads the active file and compares it to that digest. **Pre-seal check:** `_rotate` / `seal`
compares again before hashing the file into a manifest entry, and a mismatch refuses the seal. A row edited with a
recomputed `record_hash` is still caught, because the digest covers the raw bytes.

**Durable checkpoint (R1), two-phase intent/commit (C1 ruling 6483289354).** Active integrity must survive a process
crash, so the digest is not memory-only. The checkpoint holds `{committed, pending}`; each state is the `segment`
number, `byte_length` and `sha256`. Every write is an atomic replace (**temp + fsync + `os.replace`** + directory
fsync, never in place). For each append:

1. `persist_intent`: before the append, persist and fsync `{committed, pending}`, where pending is the next segment
   `n`, byte length and sha256.
2. `append_fsync`: append the bytes and fsync.
3. `commit`: promote pending to committed after **every fsynced append**.

Each tick also checks the file against it. **Startup accepts exactly two states:** file bytes == committed (the append
did not land; discard pending, `checkpoint_pending_discarded`) or file bytes == pending (the append landed; promote
pending, `checkpoint_pending_promoted`). Any partial or third state stays `active_corrupt` → gap → quarantine. A
**benign crash** between the append fsync and the commit is the pending state: it never discards a valid complete
tail, never quarantines and **never spends the automatic-recovery budget**.

**Persistence failure is fail-closed (C4 ruling 6483289354).** If the temp write, its fsync, `os.replace` (after the
repo's `PermissionError` retries, `engine._replace_retry`) or the directory fsync fails, the producer is **disabled
immediately** and `checkpoint_persist_failed` is counted. Trading is unaffected. The normal recovery path is required
before it appends again. The writer **never continues with an in-RAM digest ahead of the durable state**.

**Seal and rotation crash protocol (E1 ruling 6483743382, JSON `active_segment.seal_rotation`).** It covers both the
forced seal (bytes, rows or 6 h age) and the startup seal. The order is fixed:

1. `seal_manifest`: **seal manifest entry n durably first**. The manifest is atomically replaced and fsynced.
2. `advance_checkpoint`: **then advance the checkpoint to committed empty n+1**: `{segment n+1, length 0, sha256 of
   empty bytes}`.

Startup may recover an old checkpoint naming n **only when durable manifest entry n exactly matches** the checkpoint's
segment, length and sha256. It then advances to empty n+1 (`checkpoint_seal_advanced`) with **no quarantine and no
recovery-budget spend**. Any reverse, partial or ambiguous state **stays fail-closed** (`active_corrupt` → gap →
quarantine). Examples: the checkpoint names n+1 while the manifest lacks n, or manifest entry n mismatches the
checkpoint. **Required implementation tests:** `test_crash_between_seal_and_checkpoint_advance` (forced seal and startup
seal) and `test_seal_reverse_order_state_is_refused`.

**E1 advance proof (R1 ruling 6484022181, JSON `seal_rotation.advance_proof`).** Before E1 advances the checkpoint from
n to empty n+1, startup **hashes sealed file n** within the existing 16 MiB bound (`max_bytes_per_seal_refresh`) and
**requires exact equality with manifest entry n** (byte length and sha256). File n+1 **must be absent or empty**. A
same-size edit to file n, a non-empty n+1, a missing or over-bound file n, or any other ambiguity **fails closed as
`active_corrupt`**. Only the exact match advances, with no quarantine and no recovery-budget spend. **Required
implementation test:** `test_seal_advance_requires_exact_file_n_and_empty_next`.

**Matching (ruling 6484022181, JSON `seal_rotation.matching`).** **E1 matching uses the committed triple with pending ==
null.** A **non-null pending alongside manifest entry n stays fail-closed** (`active_corrupt`).

**Two distinct seal paths (R2 ruling 6484022181, JSON `seal_rotation.paths`).** The **forced seal** runs in a live
writer, so step 2 (`advance_checkpoint`) can fail to persist. The **startup seal** runs with no live writer, from the
pre-append state before the first append. **If forced-seal step 2 (checkpoint advance) cannot persist, C4 disables the
producer** (`checkpoint_persist_failed`), **and the next startup recovers through E1 rather than quarantine when the
manifest/file proof is exact.** **Required implementation test:**
`test_forced_seal_checkpoint_advance_persist_failure_recovers_via_e1`.

**Orphan temp files (E2 ruling 6483743382).** An orphan checkpoint temp file is **never authoritative**. Startup
ignores it and **removes it only after the canonical checkpoint is validated**. A malformed canonical checkpoint still
fails closed.

**Granularity (E3 ruling 6483743382, frozen).** **Exactly one complete canonical ledger row per intent/append/commit
transaction.** There is no batching in this first shadow implementation. The per-row durability cost is **about seven
fsync/replace operations**: the intent (temp fsync, `os.replace`, directory fsync), the append fsync, and the commit
(temp fsync, `os.replace`, directory fsync). The runtime slice must report measured append latency and overruns before
any batching proposal, and batching needs a later contract amendment. **Integrity wins over throughput while the
producer is OFF.**

**Partial append or ENOSPC (E4 ruling 6483743382, confirmed fail-closed).** Either is a **third state** and follows
`active_corrupt` → durable gap → quarantine, **including recovery-budget accounting**. There is **no truncation
salvage**, because the pending digest alone cannot prove a partial suffix safe.

| Parameter | Value | Why |
|---|---|---|
| `max_active_bytes_cap` | 16 MiB | This is `segment_bound` ≤ `SEG_MAX`, the unchanged 03A rotation. |
| `max_active_rows` | 10000 | It matches the AI-SCAN-03 basis that 16 MiB ≈ 10k rows ≈ 0.5 s to fold. |
| `max_active_age_s` | 21600 | 6 h. Age-only seals fill 4096 segments in 1024 days (≈ 2.8 y) before `manifest_full`. |
| `max_undetected_exposure_s` | 60 | One tick while the writer is alive. **Active segment only**; sealed is `sealed_max_undetected_s`. |
| `max_auto_recoveries` / `recovery_window_s` | 1 / 86400 | One automatic recovery a day. A repeat needs Integration. |

**Forced seal:** when the next row would exceed the bytes cap, when the segment reaches the row cap, or at the first
tick past the age cap (`forced_seals`). These caps also bound what a quarantine can drop. **Startup seal:** a non-empty active segment is
sealed before the first append **only when its bytes match the checkpoint** (segment, byte length and sha256, as
committed or as pending, which is promoted first) and every line parses (`startup_seals`). A **missing, malformed or mismatched checkpoint** is `active_corrupt` and takes the
recovery path below: durable gap, then quarantine (`checkpoint_mismatch`). That includes a torn or partial append
whose bytes equal neither committed nor pending; there is no salvage. **Required implementation tests:**
`test_restart_refuses_same_size_parse_valid_active_edit`, where a same-size, parse-valid edit made while the writer is
down is refused at restart; `test_crash_after_intent_before_append_accepts_committed`;
`test_crash_after_append_fsync_before_commit_promotes_pending` (no quarantine, recovery budget untouched); and
`test_torn_or_partial_append_is_active_corrupt`.

**Fail-closed recovery** (in order, JSON `active_segment.recovery`):

1. `disable`: disable the producer (`active_corrupt`) and publish ineligible. Append and seal are refused.
2. `persist_marker`: write the manifest disable marker `active_corrupt` atomically, so a restart stays disabled.
3. `open_gap`: open a durable coverage gap from the last sealed end through detection. Any window crossing it is
   ineligible (G2).
4. `quarantine`: at the next writer start, rename the active file to `seg-NNNNNN.jsonl.quarantine-<UTC>`. It is never
   deleted, truncated, sealed or read as data. Valid prefix rows are not re-imported.
5. `fresh_active`: open a fresh empty segment `n`. Clear the marker only after a full startup verification passes.
6. `rearm_limit`: at most 1 automatic recovery per 24 h. Another `active_corrupt` stays disabled until Integration
   rules.

A torn final line counts as corruption. There is no salvage; it follows the same steps.

## 6. (c) Process-private pagination pages

**Problem.** `fold_pages` trusts the page dicts that `read_page` returns.

**Rule (chosen: `process_private`).** Page dicts exist only inside one `read_window` / `fold_pages` call in one
process. `fold_pages` consumes only a lazy in-process generator over `read_page`. Pages are never stored, cached,
queued, pickled, JSON-serialized, logged or returned. The API / UI returns only the fold result. At most 1 page (≤ 64
MiB) is held per fold. Any IPC first needs a separate contract that adds authenticated page contents. One option is
per-segment canonical line boundaries that the receiver re-hashes against the verified manifest; another is a keyed
MAC. That work is DEFERRED (`ipc_page_transport`).

## 7. Counters added

`checkpoint_mismatch`, `refresh_ticks`, `refresh_overrun`, `sweeps_completed`, `eligibility_stale`, `active_digest_mismatch`, `forced_seals`,
`startup_seals`, `active_quarantined`, `recovery_refused`, `checkpoint_pending_promoted`, `checkpoint_pending_discarded`,
`checkpoint_persist_failed`, `checkpoint_seal_advanced`.

## 8. Test plan for the implementation slice

- `test_refresh_tick_verifies_at_most_sixteen_sealed_segments`
- `test_refresh_tick_holds_one_segment_buffer_at_a_time`
- `test_full_sweep_covers_every_sealed_segment_within_bound`
- `test_repeated_overrun_stays_fail_closed`
- `test_startup_verification_never_blocks_engine_or_paper`
- `test_seal_refresh_verifies_only_the_new_entry`
- `test_stale_eligibility_is_refused_on_the_hot_path`
- `test_active_same_size_edit_detected_within_one_tick`
- `test_pre_seal_digest_mismatch_refuses_the_seal`
- `test_forced_seal_at_bytes_rows_and_age`
- `test_checkpoint_atomically_replaced_after_every_fsynced_append`
- `test_crash_after_intent_before_append_accepts_committed` (required)
- `test_crash_after_append_fsync_before_commit_promotes_pending` (required)
- `test_torn_or_partial_append_is_active_corrupt` (required)
- `test_crash_between_seal_and_checkpoint_advance` (required)
- `test_seal_advance_requires_exact_file_n_and_empty_next` (required)
- `test_forced_seal_checkpoint_advance_persist_failure_recovers_via_e1` (required)
- `test_seal_reverse_order_state_is_refused` (required)
- `test_orphan_checkpoint_temp_is_never_authoritative`
- `test_one_row_per_checkpoint_transaction`
- `test_partial_append_or_enospc_is_active_corrupt`
- `test_startup_verification_reads_at_most_the_cap_with_one_buffer`
- `test_checkpoint_persist_failure_disables_the_producer`
- `test_startup_seals_a_nonempty_active_only_when_checkpoint_matches`
- `test_missing_malformed_or_mismatched_checkpoint_is_active_corrupt`
- `test_restart_refuses_same_size_parse_valid_active_edit` (required)
- `test_corrupt_active_is_quarantined_never_sealed_or_deleted`
- `test_second_corruption_in_window_stays_disabled`
- `test_pages_never_leave_the_fold`
- `test_producer_stays_default_off`

The JSON twin states what each one asserts.

## 9. DEFERRED

`runtime_producer_hook`, `producer_enable`, `ipc_page_transport`, `keyed_ledger_authentication`,
`remote_model_api_call`, `model_promotion`, `ui_controls`, `trading_authority`, `paper_order_authority`.

## 10. Resolved by Integration ruling 6480873345

There are no open questions.

- **Q1 (resolved):** Full startup verification is accepted for **PAPER/shadow only** if it runs exclusively off the
  trading thread, publishes the producer as ineligible until complete, and never blocks engine/PAPER startup. A
  bounded/indexed startup is a **pre-live optimization**, not a blocker now.
- **Q2 (resolved):** Unkeyed hashes are accepted for **PAPER only**, against corruption and uncoordinated edits. Keyed
  authentication is a **mandatory pre-live decision**. No protection is claimed against a local actor who can rewrite
  the chain.
- **Q3 (resolved):** 16 sequential sealed segments per 60 s tick: at most 256 MiB sealed + 16 MiB active, full sweep 256
  ticks = 15,360 s (4 h 16 m) at the 4096 cap. The 60 s tick, 180 s eligibility age, 6 h / 10,000-row active caps and 1
  automatic recovery per 24 h are kept. A repeated overrun past 180 s stays fail-closed. These numbers are frozen.
