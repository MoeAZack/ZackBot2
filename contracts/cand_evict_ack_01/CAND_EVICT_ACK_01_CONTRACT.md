# CAND-EVICT-ACK-01: Integration acknowledgement of evicted candidate pending-cost entries

**Status: DRAFT** (Build lane, 2026-10-30 06:47 Cairo / 04:47 UTC). Contract only: no runtime code, no settings, no state.
Not signed. Nothing in this document is authorized until Integration signs the JSON twin
(`cand_evict_ack_01_contract.v1.json`).

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
The comment id is the authority, and its author is checked by review against the audit record, not at runtime (OQ3).
PAPER only: `self.live` refuses, as in `ack_unmeasured`.

### A2. Input

```
{"row_id": "<history row id>", "reason": "<free text, 1..200 chars after strip>", "comment_id": "<digits>"}
```

There is no `net` field. An evicted row has no row left to write a net into, and this path must not invent one (A6).
Unknown extra keys are refused.

### A3. Admission (each check refuses and changes nothing)

1. Not PAPER: refused.
2. `row_id` is not a non-empty string, `reason` is blank, or `comment_id` does not match the pattern: refused.
3. The index (`state['candidate_cost_pending']`) is missing or not a dict: refused. The guard keeps failing closed.
4. `row_id` is not a key of the index: refused as **unknown**. If the id is already in the ack record, the result is
   idempotent instead (A5).
5. `row_id` is still in `self.history`: refused as **not evicted**. Those rows settle by measurement or by
   `ack_unmeasured`.
6. The entry is not a dict, or its `sleeve` is not in `CANDIDATE_SLOTS`: allowed, because malformed entries are
   exactly what fail closed for every slot. The record keeps the raw entry verbatim (OQ5).

### A4. Effect

Under `self.lock`, in this order:

1. Append an ack record to `state['candidate_cost_pending_ack']` (a new key, a dict keyed by row id):
   `{row_id, entry: <the index entry verbatim>, reason, comment_id, t: <UTC ISO seconds>, by: "integration"}`.
2. Pop `row_id` from `state['candidate_cost_pending']`.
3. `save_state()`. If the save fails (the `_save_safe` result is falsy or raises), roll back both in-memory changes:
   restore the entry and remove the record. Return a refusal. The entry stays on disk, so the slot stays fail-closed.
4. Log a WARNING line with the row id, symbol, sleeve, comment id and reason.

Exactly one index entry is removed. Nothing else in state, history, settings or the evidence store changes.

### A5. Idempotence

A second call with the same `row_id` returns `ok` with `already acknowledged (comment <id>)`. It does not write, does
not save, and does not change the stored record. That holds even when the second call carries a different comment id or
reason. A different comment id is reported in the message, and the first record stands.

### A6. What it never does

- It never re-enables a slot. `state['candidate_disabled']` and the family fail-closed reason are not touched. After the
  ack the guard simply stops finding the reason. Re-enable stays the existing separate Integration action.
- It never writes an exchange order id, trade id or client order id into the record or into state.
- It never changes a P&L figure: no `pnl_*`, `px_ack`, `pnl_fill_adj`, `cost_rev`, R, ML label, audit or cohort row is
  written, and no evidence revision is queued. The evicted close stays out of every figure, as it is today.
- It is never triggered by time, by restart, or by any automatic path. A clear by `_candidate_pending_clear` (row
  settled in history) does not write an ack record.
- It never clears an entry whose row is still in history (A3.5).

### A7. Canary interaction (f01_canary.py:bound_status), proposed answer, open (OQ1)

**Proposal: an Integration-acked evicted row does NOT count as settled for the F01 canary.** The canary gate measures
net drawdown in R. An acked evicted row has no measured net, so it cannot enter `net_r` or `drawdown_r`. Dropping it
silently would understate drawdown. For that reason:

- `bound_status` gains an `acked` input (the ack record). Any record whose `entry.sleeve == 'C-F01'` and whose
  `entry.closed >= start_utc` (or is unreadable) keeps blocking new C-F01 entries with its own text:
  `F01 canary: <n> evicted C-F01 close(s) acknowledged without a measured net - drawdown not measurable, no new C-F01
  entry (fail closed)`.
- It is counted in `closed` toward the S6 trade bound (the trade happened).
- The canary then resumes only through a new Integration-frozen canary start (`start_utc` after the acked close), which
  is already an Integration action. It never resumes through the ack itself.
- Records with `closed < start_utc` are ignored, as the index entries are today.

The alternative is to treat an acked row as settled at 0 R or at its booked net. It is rejected here because neither
figure is measured. Integration decides.

## Tests a future implementation must pass

| id | test |
|---|---|
| T1 | Evicted C-F01 entry older than 6 h: the guard disables the family. After a valid ack the entry is gone, the record exists, and the next guard pass yields no accounting reason from the index. |
| T2 | After T1, `candidate_disabled` still holds the slot and no slot is re-enabled. |
| T3 | Unknown row id: refused, state byte-identical. |
| T4 | Row id still in history (pending): refused as not evicted, the entry is kept. |
| T5 | Second identical call: ok, `already acknowledged`, no save call, record unchanged. Different comment id: same, first record kept. |
| T6 | `comment_id` that is non-numeric, empty, too short or too long, blank reason, missing or extra key: refused. |
| T7 | `save_state` returns False / raises: refusal, entry restored, no record in memory, on-disk state still holds the entry. |
| T8 | LIVE engine: refused. |
| T9 | The record and state diff hold no `orderId`, `clientOrderId`, `tradeId` or `id_fields` hit (reuse f01_canary.id_fields). |
| T10 | Totals, R, equity curve, cohort rows, evidence store and history are byte-identical before and after an ack. |
| T11 | Malformed index entry (non-dict, or unknown sleeve) can be acked, and the record keeps it verbatim. |
| T12 | Index not a dict: refused, and the guard still fails closed. |
| T13 | Restart after the ack: the entry stays cleared and the record survives the state round trip. |
| T14 | Canary (if OQ1 is accepted): an acked C-F01 record after `start_utc` blocks with the new text and counts in `closed`. Before `start_utc` it is ignored. |
| T15 | `_candidate_pending_clear` on a settled in-history row writes no ack record. |
| T16 | No time-based path clears an entry: advancing `now` by 10 years leaves it in place. |

## Open questions

- **OQ1** Canary: does an acked evicted C-F01 row stay "unmeasured, blocking" (proposal A7), or count as settled?
- **OQ2** Transport: a new PAPER API route (`/api/candidate/evict_ack`, HMAC like `/api/capital/ack`), a CLI against
  the stopped bot's state file, or both? A CLI cannot hold `self.lock` on a running bot, so the proposal is API only.
- **OQ3** Authority check: is a recorded comment id enough, with review checking the author, or must the engine (or a
  pre-commit tool) verify via `gh api` that the comment's author is the Integration account? The proposal is
  record-only, because the engine must not call GitHub.
- **OQ4** Bulk: one id per call (proposed), or a list under one comment id?
- **OQ5** Malformed entries (non-dict or unknown sleeve): ackable (proposed), or must they be repaired by a separate
  state-repair contract?
- **OQ6** Retention: does `candidate_cost_pending_ack` grow without bound (proposed: yes, it is small and is the audit
  trail), or is it capped and mirrored to an evidence file?
- **OQ7** Should the ack also be written to the RUN-PACK cohort evidence (as an audit-only line), or kept only in state
  plus the log?
- **OQ8** Should C-F02 (and any future candidate slot) use the same path? The proposal is yes, since the index is
  slot-agnostic. The canary rule in A7 is C-F01 only.

## Roadmap fit

Unblocks only the deferred OQ6 item of `factory_f01f02_family`. It does not authorize PAPER enablement, C-F02
evaluation, or any change to the 6 h rule, the guard, or the existing FILL-PNL-02 acknowledgement.
