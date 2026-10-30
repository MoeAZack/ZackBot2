# CAND-EVICT-ACK-01: Integration acknowledgement of evicted candidate pending-cost entries

**Status: DRAFT** (Build lane, 2026-10-30 06:47 Cairo / 04:47 UTC). Contract only: no runtime code, no settings, no state.
Not signed. Nothing in this document is authorized until Integration signs the JSON twin
(`cand_evict_ack_01_contract.v1.json`). Amended 2026-10-30 with Integration ruling **6515377452** (OQ1-OQ8 resolved,
see RESOLVED) and the Cowork advisories on `d84fc4a` (**6515360579**). Status stays DRAFT pending the Codex signature.

**Ordering:** implementation starts only after PR #249 (f01_canary.py) is merged. T14 is gated on f01_canary being on
master.

## Why this exists

The signed `contracts/factory_f01f02_family` (OQ6, rulings 6508164005 and 6508707059) keeps the `trend_long` family
fail-closed while any candidate close is in the durable pending-cost index and older than 6 h. A row that has been
evicted from `history[-3000:]` before it settled can never settle through the normal path, because both clear paths
need the row:

- engine.py:Engine._cost_final calls `_candidate_pending_clear(t)` only for a row it is finalizing. An evicted row is
  never finalized again.
- engine.py:Engine._candidate_pending_why reconciles only ids that are still in `self.history`. For an evicted id it
  counts age from the stored `closed` time, so the slot stays OFF for good.
- engine.py:Engine.ack_unmeasured (FILL-PNL-02, `/api/capital/ack` in app.py) looks the id up in `self.history` and
  refuses with `no closed trade <id>`. It also requires an operator-stated net, which an evicted row cannot back up.

The family contract leaves this deferred: the entry stays OFF "until a future explicit Integration acknowledgement path
clears it. It is never cleared by time." This contract specifies that path. The time rule does not change.

## Anchors (master `06379c5`)

| What | Where |
|---|---|
| Index key | engine.py `CANDIDATE_PENDING_KEY = 'candidate_cost_pending'`, `{row id: {sleeve, symbol, closed}}` |
| Set | engine.py:Engine._finish via engine.py:Engine._candidate_pending_note (an unreadable status is indexed anyway) |
| Clear (settled row) | engine.py:Engine._candidate_pending_clear, from engine.py:Engine._cost_final or engine.py:Engine._candidate_pending_why |
| Guard read | engine.py:Engine._candidate_pending_why, called from the candidate guard (`CANDIDATE_COST_PENDING_S = 21600`) |
| Operator ack (in-history rows) | engine.py:Engine.ack_unmeasured -> actual_costs.py:acknowledge (writes `px_ack`, sets `pnl_fill_adj`) |
| Persistence | engine.py:Engine.save_state -> engine.py:Engine._save_safe |
| Canary reader | f01_canary.py:bound_status (PR #249, not on master): every index entry for `C-F01` with `closed >= start_utc`, or an unreadable `closed`, goes into `pending` and blocks new C-F01 entries |

## The acknowledgement

### A1. Who may acknowledge

Integration (Codex) only. Each acknowledgement must cite a **numeric GitHub comment id** (`^[0-9]{6,12}$`) of an
Integration ruling on MoeAZack/ZackBot2 that names the row id. The engine does not call GitHub. It checks the id format
and records it. The operator HMAC on the PAPER API (the same authentication as `/api/capital/ack`) is the transport.
The runtime records the numeric comment id and **never calls GitHub** (OQ3). Acceptance and activation evidence must
resolve that id to a signed Codex comment that names the exact row id. Transport is the PAPER HMAC API only. There is no
CLI in v1 (OQ2). PAPER only: `self.live` refuses, as in `ack_unmeasured`. One row id per call (OQ4).

### A2. Input

```
{"row_id": "<history row id>", "reason": "<free text, 1..200 chars after strip>", "comment_id": "<digits>"}
```

There is no `net` field. An evicted row has no row left to write a net into, and this path must not invent one (A6).
Unknown extra keys are refused. A list of row ids is refused (OQ4).

### A3. Admission (each check refuses and changes nothing)

1. Not PAPER: refused.
2. `row_id` is not a non-empty string, `reason` is blank, or `comment_id` does not match the pattern: refused.
3. The index (`state['candidate_cost_pending']`) is missing or not a dict: refused. The guard keeps failing closed.
4. `row_id` is not a key of the index: refused as **unknown**. If the id is already in the ack record, the result is
   idempotent instead (A5).
5. `row_id` is still in `self.history`: refused as **not evicted**. Those rows settle by measurement or by
   `ack_unmeasured`.
6. The entry is not a dict, or its `sleeve` is not in `CANDIDATE_SLOTS`: allowed, because malformed entries are
   exactly what fail closed for every slot (OQ5). The raw entry is **not** stored (A4.1).
7. The path is identical for every slot in `CANDIDATE_SLOTS` (OQ8).

### A4. Effect

Under `self.lock`, in this order:

1. Add an ack record to `state['candidate_cost_pending_ack']` (a new key, a dict keyed by row id, created if absent):
   `{row_id, entry: {sleeve, symbol, closed}, entry_sha256, reason, comment_id, t: <UTC ISO seconds>, by: "integration"}`.
   `entry` holds only the whitelisted keys `sleeve`, `symbol` and `closed`. Each is kept only if it is a `str`, `int`,
   `float` (finite) or `None`, and a `str` is truncated to 64 chars. Anything else becomes `None`. Unexpected keys and
   any identifier-bearing content are never persisted (OQ5). `entry_sha256` is the sha256 of the canonical JSON
   (`sort_keys=True`, `separators=(',', ':')`, `default=repr`) of the original entry, so the original can be proved
   without being stored.
2. Pop `row_id` from `state['candidate_cost_pending']`.
3. `save_state()`. If the save fails (the `_save_safe` result is falsy or raises), roll back both in-memory changes:
   restore the entry, remove the record, and delete `candidate_cost_pending_ack` if this call created it (it was absent
   before and is now empty). In-memory state is then byte-identical to before. Return a refusal. The entry stays on
   disk, so the slot stays fail-closed.
   **A failed save is not side-effect free.** engine.py:Engine._save_safe calls engine.py:Engine._persist_fault on a
   write exception, which latches `state_untrusted['state']` and sets `ENTRIES_PAUSED` (and tries to persist settings).
   A held state file also returns False with entries kept paused. So a failed ack save may pause entries for every
   sleeve until a successful whole-state save and an operator resume. That is the existing AUD-05 fail-closed
   behaviour, and the ack path must not suppress or undo it.
4. Log a WARNING line with the row id, symbol, sleeve, comment id and reason.

Exactly one index entry is removed. Apart from the possible AUD-05 latch in step 3, nothing else in state, history,
settings or the evidence store changes.

**Concurrency:** A4 runs entirely under `self.lock`, so steps 1-3 are atomic with respect to the guard. The guard's read
of the index (engine.py:Engine._candidate_pending_why) must see either the state before the ack (entry present, no
record) or after it (entry gone, record present). It must never see a half-state (entry popped, record missing),
including on the rollback path.

### A5. Idempotence

A second call with the same `row_id` returns `ok` with `already acknowledged (comment <id>)`. It does not write, does
not save, and does not change the stored record. That holds even when the second call carries a different comment id or
reason. A different comment id is reported in the message, and the first record stands.

### A6. What it never does

- It never re-enables a slot. `state['candidate_disabled']` and the family fail-closed reason are not touched. After the
  ack the guard simply stops finding the reason. Re-enable stays the existing separate Integration action.
- It never writes an exchange order id, trade id or client order id into the record or into state. The record holds
  only the whitelisted scalar fields of A4.1, so a malformed entry carrying such ids cannot leak them (OQ5).
- It never writes or mutates cohort evidence (RUN-PACK) or the evidence store. Its only outputs are the state record and
  the WARNING log (OQ7).
- It never suppresses the AUD-05 pause that a failed save triggers (A4.3).
- It never changes a P&L figure: no `pnl_*`, `px_ack`, `pnl_fill_adj`, `cost_rev`, R, ML label, audit or cohort row is
  written, and no evidence revision is queued. The evicted close stays out of every figure, as it is today.
- It is never triggered by time, by restart, or by any automatic path. A clear by `_candidate_pending_clear` (row
  settled in history) does not write an ack record.
- It never clears an entry whose row is still in history (A3.5).

### A7. Canary interaction (f01_canary.py:bound_status), RESOLVED (OQ1, 6515377452)

**An Integration-acked evicted row does NOT count as settled for the F01 canary. It is never assumed to be 0 R.** The canary gate measures
net drawdown in R. An acked evicted row has no measured net, so it cannot enter `net_r` or `drawdown_r`. Dropping it
silently would understate drawdown. For that reason:

- `bound_status` gains an `acked` input (the ack record). Any record whose `entry.sleeve == 'C-F01'` and whose
  `entry.closed >= start_utc` (or is unreadable) keeps blocking new C-F01 entries with its own text:
  `F01 canary: <n> evicted C-F01 close(s) acknowledged without a measured net - drawdown not measurable, no new C-F01
  entry (fail closed)`.
- It is counted in `closed` toward the S6 8-close bound (the trade happened). It stays unmeasured.
- The canary then resumes only through a new Integration-frozen canary start (`start_utc` after the acked close), which
  is already an Integration action. It never resumes through the ack itself.
- Records with `closed < start_utc` are ignored, as the index entries are today. Only a later Integration-frozen start
  after that close excludes it.
- This scoring rule is C-F01 only (OQ8). T14 is gated on f01_canary being merged (#249).

## Tests a future implementation must pass

| id | test |
|---|---|
| T1 | Evicted C-F01 entry older than 6 h: the guard disables the family. After a valid ack the entry is gone, the record exists, and the next guard pass yields no accounting reason from the index. |
| T2 | After T1, `candidate_disabled` still holds the slot and no slot is re-enabled. |
| T3 | Unknown row id: refused, state byte-identical (no empty `candidate_cost_pending_ack` left behind). |
| T4 | Row id still in history (pending): refused as not evicted, the entry is kept. |
| T5 | Second identical call: ok, `already acknowledged`, no save call, record unchanged. Different comment id: same, first record kept. |
| T6 | `comment_id` that is non-numeric, empty, too short or too long, blank reason, missing or extra key, or a list of row ids: refused. |
| T7 | `save_state` returns False / raises: refusal, entry restored, no record in memory, a newly created empty `candidate_cost_pending_ack` deleted (in-memory state byte-identical), on-disk state still holds the entry. On the write-exception path `state_untrusted['state']` is latched and `ENTRIES_PAUSED` is True: the ack does not suppress the AUD-05 pause. |
| T8 | LIVE engine: refused. |
| T9 | The record and state diff hold no `orderId`, `clientOrderId`, `tradeId` or `id_fields` hit (reuse f01_canary.id_fields). |
| T10 | Totals, R, equity curve, cohort rows, evidence store and history are byte-identical before and after an ack. |
| T11 | A malformed index entry (non-dict, unknown sleeve, extra keys including `orderId`, nested values) can be acked. The record holds only whitelisted scalar `sleeve` / `symbol` / `closed` plus an `entry_sha256` equal to the sha256 of the canonical original. It holds no extra key or nested value. |
| T12 | Index not a dict: refused, and the guard still fails closed. |
| T13 | Restart after the ack: the entry stays cleared and the record survives the state round trip. |
| T14 | Canary (gated on #249 / f01_canary on master): an acked C-F01 record after `start_utc` blocks with the new text and counts in `closed` toward the 8-close bound. It is never folded into `net_r` / `drawdown_r` as 0 R. Before `start_utc`, or under a later frozen start, it is ignored. A C-F02 record never affects the canary. |
| T15 | `_candidate_pending_clear` on a settled in-history row writes no ack record. |
| T16 | No time-based path clears an entry: advancing `now` by 10 years leaves it in place. |
| T17 | Guard/ack interleaving: with the guard (`_candidate_pending_why`) run from another thread against an ack (success, and save failure with rollback), every guard observation is either (entry present, no record) or (entry gone, record present). It never sees the entry popped with the record missing. A4 holds `self.lock` throughout. |
| T18 | The ack path writes no cohort / evidence-store file and calls no GitHub / network client. |
| T19 | The same path acks a C-F02 entry (all `CANDIDATE_SLOTS`). |
| T20 | The ack record is unbounded: 1,000 acks leave 1,000 records (no cap, no eviction). |

## RESOLVED (Integration ruling 6515377452; Cowork advisories 6515360579)

There are no open questions.

- **OQ1** Canary: A7 accepted. An acked evicted C-F01 close after the frozen start counts toward the 8-close bound,
  stays unmeasured and blocks new C-F01 risk. Only a later Integration-frozen start after that close excludes it.
  Never assume 0 R.
- **OQ2** Transport: PAPER HMAC API only. No CLI in v1.
- **OQ3** Authority: the runtime records the numeric comment id and never calls GitHub. Acceptance and activation
  evidence must resolve that id to a signed Codex comment naming the exact row.
- **OQ4** One row id per call.
- **OQ5** Malformed entries are ackable but not recorded verbatim. Only whitelisted, scalar-safe sleeve / symbol /
  closed values are kept, plus the sha256 of the canonical original entry. No raw unexpected keys or identifier-bearing
  content (A3.6, A4.1, A6 and T9/T11 are aligned).
- **OQ6** The record is a durable, unbounded state audit in v1, with no cap or eviction.
- **OQ7** Outputs are the state record and the WARNING log only. The ack path never writes or mutates cohort evidence.
- **OQ8** The same path serves all `CANDIDATE_SLOTS`. A7's scoring rule is C-F01 only.
- **Cowork advisories (6515360579):** a failed save is not side-effect free (`_save_safe` -> `_persist_fault` latches
  `state_untrusted` and sets `ENTRIES_PAUSED`). This is folded into A4.3, A6 and T7. Rollback also removes a newly
  created empty ack dict (T3/T7). Guard/ack interleaving is covered by A4 Concurrency and T17. Implementation is ordered
  after #249, and T14 is gated on f01_canary.

## Roadmap fit

Unblocks only the deferred OQ6 item of `factory_f01f02_family`. It does not authorize PAPER enablement, C-F02
evaluation, or any change to the 6 h rule, the guard, or the existing FILL-PNL-02 acknowledgement.
