# RT-01 replacement entrypoint and runtime wiring contract

**State:** first-pass proposal by Claude Projects for Codex review (issue #219 comment 6644245688). Base: protected
master `54c41dc6fe60c432bd0ce70dabb99dd3f06ee1d8`. Times are Africa/Cairo.

## Roadmap fit

Wave C exit requires that NEWCORE "runs the complete vertical slice without invoking the legacy engine". RT-01 is the
composition root for that: one entrypoint that wires the existing pure modules together. It is step 5 of the Wave C
critical path in the 4 November handoff. It installs nothing, activates no PAPER cohort and opens no network
connection.

## Boundary

- **New files only:** `newcore/runtime/` (`config.py`, `ports.py`, `runtime.py`, `__main__.py`), this contract and
  `tests/newcore_runtime/`.
- **Imports:** NC-01 domain, NC-02 store, NC-04 account context, NC-05 scheduler, NC-06 risk, NC-07 management, NC-09
  observability, REC-02 reconciliation and the stdlib. It imports no legacy module, `newcore.tnet02` or
  `newcore.venue.http_sender`, and no legacy module imports it.
- **Not edited:** every existing module, including `newcore/risk.py` (RISK-LOSS-01 runs in parallel) and the
  #291/#292/#293/#294 files.

## Configuration (`newcore.runtime.config`)

- `RuntimeEnvironment` has exactly two values: `paper_sim` and `testnet`. Any environment containing `live`,
  `mainnet`, `prod` or `real` is refused with `environment_forbidden`.
- `RuntimeConfig` is frozen. It holds 1 to 4 `AccountSpec` rows (`acct_` id, `pf_` aggregate id, account directory,
  egress id), with distinct ids and directories. Directories are compared after `normpath`, with slashes normalized
  and case folded, so `/x/./b/../a` duplicates `/x/a`.
- Bounds:
  - `max_snapshot_age_s` 1..60;
  - `evidence_deadline_ms` 100..60,000;
  - `refresh_period_ms` 1,000..60,000;
  - `proof_max_age_ms` 1,000..120,000, and at least refresh period plus evidence deadline;
  - `stop_grace_ms` 1,000..60,000 (a zero grace would let NC-04 stop cancel queued protection at once).
- `entries_enabled` defaults to `False`.
- `config_from_mapping` refuses unknown keys, a wrong schema, and any live, mainnet or credential key
  (`live_confirm`, `api_key`, `secret` and so on) at the top level or in an account row. It never ignores them.
- The configuration never carries a credential.

## Ports (`newcore.runtime.ports`)

Each account supplies an `AccountPorts` value:

| Port | Role | RT-01 default |
|---|---|---|
| `context` | That account's own NC-04 `AccountContext` (exact type) | required |
| `state_source.load(account_id, journal_state)` | Projects the NC-02 `JournalState` into REC-02 `DurableState` | required |
| `evidence.read_work` / `assemble` | Names read-only venue work for one generation; turns completed results into a REC-02 `VenueSnapshot` | required |
| `management.evaluations` | NC-07 inputs for every managed lot | `NoManagedLots` |
| `entries.candidates` | NC-06 opening requests | `NoEntries` |
| `actions.accept_management` / `accept_entry` | The single future route to journaled venue work | `RefusingActionSink` |

No port receives a venue, budget, credential or raw payload from the runtime.

## Boot (`newcore.runtime.boot`)

Wiring is checked before any journal is opened or any venue is called:
- `boot_id` is a 1..64 character string;
- the port keys equal the configured accounts;
- each context's account and egress match its spec;
- each context is fresh: nothing queued, nothing in flight, no results, not stopping, no reconcile flag. Leftover
  work would otherwise be sent before any exchange-truth proof (`account_context_not_fresh`);
- NC-04 `validate_distinct_egress` passes. It only enforces distinct egress for live/mainnet, so on testnet and
  PAPER_SIM two accounts may share an egress.

A failure raises `RuntimeBootError`. If anything raises after journals were recovered (for example scheduler
construction), every opened journal is closed before the exception propagates, so no writer lock leaks.

Then, per account, in order. The first failure becomes that account's hold, and later steps are skipped. Any
unexpected exception inside one account's boot (for example an NC-04 internal error) becomes the sticky `boot_error`
hold for that account only; the other accounts still boot.

1. **NC-02 `recover_journal`.** CLEAN or REPAIRED continues. MISSING, DAMAGED, UNREADABLE, ABORT_RO and
   DURABILITY_UNAVAILABLE each map to their own `journal_*` hold. `JournalLocked` gives `journal_locked`, and any
   other exception gives `journal_error`. A missing journal is never created.
2. **Durable truth.** `state_source.load` must return an exact `DurableState` for this account
   (`durable_state_invalid`), with a CONFIRMED binding (`binding_unconfirmed`). The binding environment must be
   `testnet` for TESTNET and `sim` for PAPER_SIM. Mainnet, or any other mismatch, gives `environment_mismatch`.
3. **Read-only evidence.**
   - `read_work` must return 1 to 32 unique `VenueWork` items. Each must have `Action.RECONCILE`, a positions,
     open-orders or fills operation, a deadline within the evidence deadline, and a `work_id` never used before in
     this process for this account (and unknown to the context's results). A reused id could otherwise fold an old
     generation's results as a new proof. Anything else gives `evidence_work_invalid` before any venue call.
   - The work runs synchronously through the account's own NC-04 context. No scheduler thread exists yet.
   - Every result must be COMPLETED (`evidence_unavailable`), and `assemble` must return an exact `VenueSnapshot`
     (`evidence_invalid`).
4. **REC-02 `plan_reconciliation`** with the wall clock and `max_snapshot_age_s`. FLAT_CONFIRMED or
   PROTECTED_CONFIRMED proves restore. HOLD gives `reconcile_hold`, and an exception gives `reconcile_error`. A worker
   that ends boot with `reconcile_before_restart` gives `worker_reconcile_before_restart`.

Every account, held or not, is handed to one NC-05 `Scheduler` with `restore_proven` set from step 4.

## Hooks (run on that account's NC-05 runner thread only)

**guards (1 s):**
- If the NC-04 worker, or the published NC-05 row for this account, reports `reconcile_before_restart`, hold.
- Any sticky hold makes guards refuse, and no further refresh starts.
- Otherwise the refresh runs in two phases:
  1. Queue a new evidence generation through `Scheduler.submit` once every `refresh_period_ms`.
  2. On a later pass, when every result exists, fold it through REC-02.
- A confirmed plan clears the clearable holds and records a fresh proof. If restore was not yet proven, it asks the
  owner thread to call `mark_restore_proven`.
- A read that has not finished by its deadline is cancelled if still queued and gives `evidence_timeout`. A read that
  finishes past its deadline comes back UNKNOWN, which NC-04 latches as `reconcile_before_restart`.
- Guards succeed only with no hold and a proof younger than `proof_max_age_ms`. Otherwise they refuse, so the account
  is not ready and cycle is never admitted.

**manage (2 s):** runs for held accounts too, so that protection is never blocked by a hold.
- `management.evaluations` must return a tuple of exact `ManagementEvaluation`. Otherwise
  `management_input_invalid` applies and the hook fails.
- Each result goes through NC-07 `evaluate`:
  - A plan with status RECONCILE or AMBIGUOUS, reason INPUT_INVALID or PROTECTION_UNKNOWN, or status ACTIONS with no
    action, gives `management_unresolved`.
  - A plan without actions passes only for NO_CHANGE, PENDING_INTENT or LOT_FINISHED (an allow-list). Every other
    HOLD reason (stale input, generation mismatch, confirmation pending, ambiguous touch and so on) refuses this
    manage pass and records `not_ready:<reason>`, so the account is not ready until a later pass succeeds.
  - A plan with actions must be accepted by the sink with a literal `True`. Any other answer gives
    `management_unrouted`, and an exception gives `action_sink_error`.
  - A passing plan is traced with NC-09 `trace_plan(WHY_HELD)` best effort. A trace failure is only recorded.

**Any hook:** an unexpected exception becomes the sticky `hook_error` hold and a failed outcome. It is never left
to the scheduler alone.

**cycle (60 s):**
- With entries paused, the hook succeeds without calling the entry source or NC-06.
- With entries enabled, it needs no hold on the account, a fresh proof, and the published scheduler snapshot ready for
  every account. Otherwise it refuses and records `entries_held`.
- Each candidate must be an exact `RiskEvaluation`, or the hook fails and records `entry_source_invalid` (or
  `entry_source_error`). NC-06 `evaluate` runs on each one, and only an
  `ApprovedDecision` reaches `accept_entry`.
- RT-01's sink refuses, so the outcome is recorded as `entry_unrouted` and nothing is sent.

## Holds

- **Clearable** by a later confirmed proof: `evidence_unavailable`, `evidence_timeout`, `evidence_invalid`,
  `reconcile_error`, `reconcile_hold`.
- **Sticky** for the life of the process (they need a restart and a fresh boot proof): every `journal_*` hold,
  `durable_state_invalid`, `binding_unconfirmed`, `environment_mismatch`, `evidence_work_invalid`,
  `worker_reconcile_before_restart`, `boot_error`, `hook_error`, and every `management_*` and `action_sink_error`
  hold.

## Snapshots, stop and close

- `Runtime.snapshot()` is a lock-free read of a frozen `RuntimeSnapshot`. It holds the NC-05 snapshot plus, per
  account: phase (`hold` / `proven` / `stale`), sorted hold codes, journal verdict, restore flag, proof time, last
  REC-02 status and id, and evidence generation.
- It also holds the last 256 sanitized `RuntimeEvent` rows and a dropped-event count.
- `step()` and `stop()` are owner-thread calls. `step()` checks "restore due and no hold", calls
  `mark_restore_proven` and sets the restore flag all under the account lock, so a hold from the runner cannot
  interleave. `stop` uses NC-05 bounded stop semantics.
- `close()` releases the journal writer locks and is refused until the scheduler publishes STOPPED.

## CLI

`python -m newcore.runtime check --config <file.json>` (utf-8):
- It validates the configuration and prints a JSON boot plan.
- It refuses, with exit 2, when `LIVE_CONFIRM`, `ZACKBOT_LIVE` or `ZACKBOT_MAINNET` is present in the environment,
  in any letter case and even with an empty value.
- Unreadable, malformed or pathologically nested JSON is a `config_unreadable` refusal, never a traceback.
- It is read-only: no journal, no lock, no credential, no network. A UNC or `//` account directory is reported as
  `null` instead of being probed.
- There is deliberately no `run` command.

## Acceptance evidence

`tests/newcore_runtime/` (122 tests) covers:
- boot with real NC-02 journals on disk, real NC-04 contexts and budgets, real REC-02 plans, and real NC-05 threads;
- every journal verdict;
- binding and environment refusal;
- order-capable evidence work refused before any venue call;
- reconcile hold, then recovery;
- refresh, foreign position, in-flight deadline;
- two-account readiness gating;
- the management route and sink answers;
- entries paused or enabled;
- wiring refusals, stop and close, immutability;
- config and CLI refusals;
- the legacy and network import boundary;
- a regression test for each mutation survivor and each adversarial-review defect.

No test calls a venue write. Every scripted venue asserts that no submit or cancel was made.

## Deferred (later tickets)

- A concrete `DurableStateSource` projection from the NC-02 fold.
- A concrete `EvidencePort` over the NC-03 testnet venue. It needs #291's complete classic and algo order truth.
- A journaling `ActionSink` that turns NC-07 and NC-06 outputs into NC-01 intents through the NC-02 grammar.
- NC-09 queue persistence.
- The `run` command, the PAPER/testnet install, and the restart, rollback and continuity drills.
- UI.
