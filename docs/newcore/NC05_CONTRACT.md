# NC-05 Scheduler Contract

Status: frozen implementation contract. Source: ZackBot2 issue #219, comment
`6616816855`. Base: protected master
`d376264a94061cbbb81354f58b9f6837b2d93320`.

## Boundary

NC-05 is a deterministic scheduling kernel. It has one caller-owned scheduler
thread and one dedicated `AccountContext` runner per account, with at most four
accounts. It does not own a runtime loop, sleeps, persistence, venue transport,
credentials, UI, PAPER activation, or mainnet behavior. AccountContext, ports,
budgets, and stores remain NC-04/NC-02 owned.

The only downstream strategy surface is the typed `SchedulerHooks` protocol.
Every invocation returns a typed `HookOutcome`; exceptions and invalid returns
are reduced to stable failure codes. Hooks cannot receive venue ports, budgets,
or raw payloads through this API.

## Cadence and priority

Default monotonic cadences are:

- guards: 1,000 ms
- manage: 2,000 ms
- cycle: 60,000 ms

Priority is queued protection/flatten, guards, manage, cycle, general NC-04
work, then publication. A time jump schedules at most one invocation for each
account/lane and advances the next deadline from the observed time; missed
periods are skipped and never burst-replayed. Each account/lane has at most one
queued or in-flight hook. Accounts never share an executor, so a blocked hook
for one account cannot block another account.

`Scheduler.submit` routes `VenueWork` only to the matching NC-04 worker. A
protection or flatten submission wakes that account runner immediately. Submit,
snapshot, `run_next`, and stop are all invoked only by that dedicated runner;
the scheduler thread exchanges sanitized immutable events with it and never
calls the worker. The runner checks protection/flatten before any hook whenever
it selects new work.

## Startup, readiness, and stale state

Every process boot supplies a new `boot_id`. Generation and monotonic times are
rebuilt in memory and are never persisted. An account is not ready until NC-04
restore is proven and its first successful guards and manage passes finish.
Cycle hooks are not admitted until every configured account is ready.

Lane success is stale after guards 3,000 ms, manage 6,000 ms, and cycle 120,000
ms. A missing first success is stale. Published data is expected to be replaced
within 50 ms of an owner step and no later than 250 ms after a transition; the
runtime integration must therefore call `step()` frequently enough to satisfy
that bound. Publication older than 1,000 ms is stale until the next owner step.

## Snapshots

`snapshot()` performs one reference read and takes no lock. Only the scheduler
owner publishes. `SchedulerSnapshot` and every nested public value are frozen
and contain only immutable primitives/tuples: boot ID, generation,
`published_mono`, scheduler state, readiness/stale flags, stable failure codes,
per-account lane state, reconcile state, and sanitized queue summaries. No
reader receives a live AccountContext, hook, budget, venue, result payload, or
mutable collection.

## Stop and restart safety

Stop rejects all new scheduler submissions and clears pending hooks. Each
account runner drains already-queued protection/flatten work only until the
deadline, then calls `AccountContext.stop(deadline_ms)` to cancel the remaining
queue and record NC-04 shutdown state. No new hook is selected after stop begins. A hook or account operation that
finishes at or after the stop deadline—including `run_next` and stop itself—marks the account
`reconcile_before_restart`; an exception or uncertain worker snapshot does the
same. Such a stop is never represented as clean. Restart remains fail-closed
until NC-04 restore/reconciliation is proven and new first guards/manage passes
succeed.

## Acceptance evidence

Focused tests must use a fake monotonic clock and cover exact cadence, large
time jumps/no bursts, priority, blocked-account isolation, concurrent lock-free
readers, immutable snapshots, sanitized failures, stale/readiness gates,
bounded stop and late completion, restart gating, import boundaries, and
latency probes. The full NEWCORE suite runs once on the final exact head.
