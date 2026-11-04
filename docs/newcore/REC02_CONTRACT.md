# REC-02 integrated exchange-truth reconciliation contract

**State:** pure-planner first slice frozen by Codex.  Canonical roadmap ticket:
Wave C10 / REC-02 (not NC-10).  Base: protected master
`87b9b91aec48da37c454c98453095b3344e79f73`.

## Roadmap fit and seam

REC-02 follows NC-01 through NC-09 and precedes TNET-01.  Its pure module has
one interface: `plan_reconciliation(durable, snapshot, config)`.  The caller
supplies immutable durable local truth, a typed exhaustive venue snapshot, and
the typed `max_snapshot_age_s` policy.  The module returns one immutable plan.
It performs no network, disk, journal, scheduler, runtime, PAPER, UI, credential,
or exchange action.

Later adapters will gather evidence through NC-03/NC-04, schedule through NC-05,
make accepted plans durable through NC-02, translate protection/flatten work
through NC-01/NC-07, replay it through NC-08, and mirror it best-effort through
NC-09.  Those adapters may not weaken this contract.

## Frozen evidence rules

1. The local `Account` binding must be `CONFIRMED`.  Every evidence page must
   name that exact account and binding.
2. Positions, open orders, and user trades are all mandatory.  Each component
   starts at page 1, has contiguous page numbers, is monotonic, and explicitly
   marks only its final page complete.  All pages carry one snapshot generation.
3. No component may predate the durable state's last venue observation, exceed
   `now_ms`, or be older than the caller-supplied positive
   `max_snapshot_age_s`.  The pure module contains no runtime age constant.
4. Missing, incomplete, mixed-generation, backward, future, stale, wrong-kind,
   duplicate, malformed, or contradictory evidence yields only fail-closed
   `HOLD`.  It never proves an empty account and derives no action from an
   incoherent snapshot.
5. Venue orders are owned only by an exact durable client-order identity; an
   existing exchange-order identity must also agree.  Caller claims cannot turn
   an unknown order into an owned order.
6. External reductions may be proposed for durable adoption only when the
   confirmed binding, exact position delta, and exhaustive typed trade evidence
   agree.  Every adoption trade is strictly newer than the durable last venue
   observation and no later than its coherent trade-page observation or `now`.
   Late/partial owned fills additionally require exact client-order identity,
   an ADD/REDUCE effect compatible with the durable purpose, exact equality to
   the newly executed delta, and total executed quantity no greater than the
   request.  Applying or journaling that proposal is deferred.
7. A venue-only position/order or quantity above durable ownership is foreign or
   a manual add.  It is quarantined, entries remain held, and it is never silently
   adopted.  A known owned position with an external add may receive only a
   protection proposal for the corroborated venue quantity.
8. Protection counts only an open, reduce-only stop whose client identity is
   durably owned as `Purpose.PROTECT`; foreign stops never satisfy coverage.
   Missing/undersized protection holds.  `FLATTEN_REQUIRED` is possible only when
   the exact fresh, bounded owned protective-stop delta explains the exact
   corroborated residual position.  Historical stop-trade presence, a wrong
   effect, or a non-equal delta cannot authorize flatten.  Contradictory evidence
   yields HOLD without flatten.

## Closed outcomes and determinism

`FLAT_CONFIRMED` requires exhaustive coherent evidence with no venue position or
open order and no unresolved local difference.  `PROTECTED_CONFIRMED` requires
exact owned position/order agreement and full owned protection coverage.  Every
other result is `HOLD` with closed typed findings/actions and an existing
`ReasonCode`.

The planner reconstructs every exact input into owned frozen values, rejects
subclasses and wrong raw types (including booleans as integers), canonicalizes
finding/action order, and derives `rec_` identity from canonical output bytes.
Equal inputs therefore produce equal plans independent of caller ordering, and
later caller mutation cannot alter a returned plan.

## Acceptance matrix

- coherent flat and protected long/short accounts;
- local-only and venue-only positions/orders;
- partial and late fills, including stop fill while offline;
- missing/duplicate orders and duplicate positions/trades;
- manual close/reduction and manual add;
- missing/undersized/foreign protection;
- stale, future, mixed-generation, backward, incomplete and paginated evidence;
- restart/idempotency (same durable generation and snapshot produce one plan);
- exact-type, forged/mutable-input and non-finite Decimal refusal;
- no networking, file access, sleep, threads, secrets, legacy or runtime imports.

## Explicit deferral

NC-02 journal application, NC-03/04 querying and rate budgets, NC-05 scheduling
and readiness publication, NC-07 action translation, NC-08 replay integration,
NC-09 emission/writers, retry policy, runtime/PAPER/testnet wiring, TNET-01,
alerts/UI, credentials, automatic exchange remediation, legacy-engine changes,
mainnet and real funds are outside this slice.
