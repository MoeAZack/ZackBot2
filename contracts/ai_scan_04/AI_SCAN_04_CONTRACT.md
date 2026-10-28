# AI-SCAN-04 — producer wiring prerequisites (PAPER / shadow only)

**Status: DRAFT — unsigned.** This is a contract only. It adds no runtime code and grants no authority. The machine
twin `ai_scan_04_contract.v1.json` sits next to this file and is authoritative; the JSON wins any conflict. Tests read
the JSON and check this file against it. Assignment: Codex 6468672436, which set three pre-producer requirements
(a), (b) and (c). Built from `origin/master` @ `50ec4e3`. `builds_on` pins the **accepted merge SHAs**: AI-SCAN-03
(#227) @ `612b537` and AI-SCAN-03A (#229) @ `f756068`. Neither is edited here.

**Every number below is a proposal for Integration.** Each one is justified in its section.

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
(`keyed_ledger_authentication`). Readers do not rely on eligibility: `read_page` re-hashes every sealed segment it
reads. Requirement (a) therefore bounds how long the **producer** can keep appending after an edit. It does not
protect reads, which are already protected.

## 4. (a) Bounded periodic integrity refresh

**Problem.** Eligibility stays True after a sealed-segment edit until the next `refresh()`, and `append()` never
re-verifies sealed files. The 03A `refresh()` reads every sealed byte, up to 64 GiB, after every seal.

**Rule.** The rolling refresh tick runs on the writer thread only.

| Parameter | Value | Why |
|---|---|---|
| `tick_period_s` | 60 | It matches the 60 s C1 heartbeat, so one writer timer drives both. |
| `sealed_segments_per_tick` | 4 | This is `SEGS_PER_READ`, so a tick costs the same as one verified read page. |
| `max_sealed_bytes_per_tick` | 64 MiB | 4 × `SEG_MAX`. That is about 1.1 MiB/s on average, which is light for any disk. |
| `max_active_bytes_per_tick` | 16 MiB | The active digest check reads at most `segment_bound`. |
| `max_segment_bytes_per_tick` | 80 MiB | Sealed plus active. The manifest is extra and bounded by 4096 entries. |
| `max_full_sweep_ticks` / `max_full_sweep_s` | 1024 / 61440 | ceil(4096 / 4) × 60 s ≈ 17.1 h at the cap. At 100 segments it is 25 min. |
| `max_bytes_per_seal_refresh` | 16 MiB | A seal verifies only the manifest and the new entry, never the corpus. |
| `eligibility_max_age_s` | 180 | 3 ticks. If the writer stalls or dies, the hook fails closed. |

Each tick does four things. It reloads the manifest and requires that its `manifest_hash` matches the writer's copy. It
verifies the next 4 sealed entries at a rolling cursor that wraps. It checks the active digest (§5). Then it publishes a
generation stamped with `time.monotonic()`. Any mismatch disables the producer. Ticks never overlap; a skipped slot is
counted `refresh_overrun`. The hook reads one cached tuple plus one monotonic clock. A generation older than 180 s is
ineligible (`eligibility_stale`), so G4 stays intact: no disk I/O, hashing or manifest traversal on the trading thread.
Startup still verifies the full corpus once per start to build the restart index (open question Q1).

## 5. (b) Active-segment exposure and fail-closed recovery

**Problem.** The active segment is not hash-bound. A same-size edit to rows already written goes undetected, and
`seal()` would then bind the edited bytes into the manifest.

**Rule.** The writer keeps an in-memory **running digest**: sha256 state plus length, over exactly the bytes it
appended. Every tick re-reads the active file and compares it to that digest. **Pre-seal check:** `_rotate` / `seal`
compares again before hashing the file into a manifest entry, and a mismatch refuses the seal. A row edited with a
recomputed `record_hash` is still caught, because the digest covers the raw bytes.

| Parameter | Value | Why |
|---|---|---|
| `max_active_bytes_cap` | 16 MiB | This is `segment_bound` ≤ `SEG_MAX`, the unchanged 03A rotation. |
| `max_active_rows` | 10000 | It matches the AI-SCAN-03 basis that 16 MiB ≈ 10k rows ≈ 0.5 s to fold. |
| `max_active_age_s` | 21600 | 6 h. Age-only seals fill 4096 segments in 1024 days (≈ 2.8 y) before `manifest_full`. |
| `max_undetected_exposure_s` | 60 | One tick while the writer is alive. |
| `max_auto_recoveries` / `recovery_window_s` | 1 / 86400 | One automatic recovery a day. A repeat needs Integration. |

**Forced seal:** when the next row would exceed the bytes cap, when the segment reaches the row cap, or at the first
tick past the age cap (`forced_seals`). These caps also bound what a quarantine can drop. **Startup seal:** the digest
does not survive a restart, so a non-empty active segment that parses cleanly is sealed before the first append
(`startup_seals`).

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

`refresh_ticks`, `refresh_overrun`, `sweeps_completed`, `eligibility_stale`, `active_digest_mismatch`, `forced_seals`,
`startup_seals`, `active_quarantined`, `recovery_refused`.

## 8. Test plan for the implementation slice

- `test_refresh_tick_verifies_at_most_four_sealed_segments`
- `test_full_sweep_covers_every_sealed_segment_within_bound`
- `test_seal_refresh_verifies_only_the_new_entry`
- `test_stale_eligibility_is_refused_on_the_hot_path`
- `test_active_same_size_edit_detected_within_one_tick`
- `test_pre_seal_digest_mismatch_refuses_the_seal`
- `test_forced_seal_at_bytes_rows_and_age`
- `test_startup_seals_a_nonempty_active_before_append`
- `test_corrupt_active_is_quarantined_never_sealed_or_deleted`
- `test_second_corruption_in_window_stays_disabled`
- `test_pages_never_leave_the_fold`
- `test_producer_stays_default_off`

The JSON twin states what each one asserts.

## 9. DEFERRED

`runtime_producer_hook`, `producer_enable`, `ipc_page_transport`, `keyed_ledger_authentication`,
`remote_model_api_call`, `model_promotion`, `ui_controls`, `trading_authority`, `paper_order_authority`.

## 10. Open questions for Integration

- **Q1:** Startup still verifies every sealed byte (up to 64 GiB at the cap) to build the restart index. Accept it as
  a once-per-start cost, or require a bounded startup (an index sidecar) before the producer is enabled?
- **Q2:** Is the unkeyed threat model acceptable for PAPER, with `keyed_ledger_authentication` deferred?
- **Q3:** Tick 60 s, 4 segments per tick (17.1 h worst-case sweep), active age 6 h, 10000 rows, 1 auto-recovery per
  24 h: accept or retune?
