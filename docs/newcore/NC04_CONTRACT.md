# NC-04 AccountContext and account-isolated order budget

**State:** first slice frozen for implementation after NC-03B.  This file
freezes the narrow rate-evidence seam and the decisions that must be made
before the worker/budget slice starts.  It does not authorize runtime wiring.

## 1. Position in the roadmap

NC-04 follows NC-03 and precedes the NC-05 scheduler.  It will own one worker,
priority queue, request budget and rate/ban state per account.  The queue uses
NC-01's existing `Action` and `PRIORITY`, then FIFO enqueue order.  NC-04 calls
the accepted NC-03 `VenuePort`; it does not interpret exchange truth, mutate a
Portfolio or Journal, retry a venue call, schedule strategies, reconcile, size
risk or manage positions.

REC-02 depends on NC-04 through NC-09.  TNET-01 follows REC-02.  Neither is part
of this slice.

## 2. First slice: sanitized rate evidence only

The NC-03 sender may return only these optional numeric fields in addition to
status and body:

```python
HttpResponse(
    status: int,
    body: bytes,
    retry_after_ms: int | None = None,
    used_weight_1m: int | None = None,
)
```

The concrete HTTPS sender reads `Retry-After` and
`X-MBX-USED-WEIGHT-1M` case-insensitively.  A field is evidence only when it
occurs exactly once and its semantic value is a non-empty unsigned ASCII
decimal integer within signed 64-bit range.  `Retry-After` is accepted only in
delta-seconds form and converted exactly to milliseconds; dates, signs,
fractions, non-ASCII digits, duplicates and overflow are ignored.  HTTP parser
normalization of legal optional whitespace is not a second value.  No raw or
unrecognized response header can cross this boundary.

The adapter exposes:

```python
RateEvidence(
    http_status: int,             # 100..599
    retry_after_ms: int | None,   # 0..2**63-1
    used_weight_1m: int | None,   # 0..2**63-1
    observed_at_ms: int,
)

BinanceUsdMTestnetVenue(..., rate_observer: Callable[[RateEvidence], None] | None = None)
```

After any structurally valid `HttpResponse`, the adapter calls the observer
exactly once, immediately and before interpreting the status or body.  This
includes 3xx, 418, 429, 5xx and malformed JSON.  A DNS, connect, TLS, send,
header or body transport failure produces no evidence because no complete
response exists.  An observer failure makes that venue call `UNKNOWN` with
stable detail `rate_observer`; it never retries and never returns the venue
answer as trusted.  `VenuePort` signatures and outcome records do not change.

Rate evidence is observation, not authority.  Missing or malformed fields are
unknown, never zero.  This slice does not decide whether a response is an
authentic ban, calculate capacity, sleep, reserve weight or release a queue.

## 3. Frozen worker interface (not implemented in this slice)

The later worker slice is limited to:

- frozen `VenueWork(work_id, action, operation, payload, enqueued_at_ms,
  deadline_ms)`; operation is exactly `SUBMIT_MARKET`, `SUBMIT_STOP`, `CANCEL`,
  `QUERY`, `POSITIONS`, `OPEN_ORDERS` or `FILLS`, and payload is the existing
  typed NC-03 value for that operation--never an arbitrary callable;
- `AccountContext(account_id, venue, budget, monotonic_clock)` with
  `submit(work)`, immutable `snapshot()` and bounded `stop(deadline_ms)`;
- `AccountBudget.reserve(operation, action, now_ms)`,
  `observe(RateEvidence)` and immutable `snapshot()`;
- one account-local worker, queue, budget and ban state; a work item calls the
  VenuePort zero or one time.

WAIT, SKIP, RESUME, HALT and PAUSE create no venue work.  Expired or cancelled
queued work sends nothing.  UNKNOWN is never converted to empty or success.

## 4. Decisions required before worker implementation

These are intentionally unresolved.  The worker PR may not invent them:

1. the versioned endpoint-cost table, weight window and authoritative source;
2. protection-reserve arithmetic, replenishment and exact saturation behavior;
3. the mapping from `Action` plus operation to reservations, especially
   reconcile reads and cancels;
4. the policy that maps 418, 429, `Retry-After`, used-weight evidence and
   missing/malformed evidence to account-local blocked states;
5. whether and how a ban/retry deadline survives process restart, and which
   durable record owns it;
6. how an exchange/IP-wide response coexists with the frozen requirement that
   one account cannot delay another account's protection;
7. queue deadline/cancellation races and the precise shutdown result when an
   in-flight one-shot venue call reaches its own deadline.

Each decision needs deterministic tests and an explicit Codex ruling before
worker code.  No generic token-bucket defaults, guessed Binance weights or
implicit authenticity rule are accepted.

## 5. Acceptance

This first slice requires local-only tests proving strict header sanitation,
observer ordering and cardinality, 418/429/3xx/5xx/malformed-body observation,
no observation on transport failure, fail-closed observer failure, unchanged
VenuePort outcomes, no retry and no arbitrary-header exposure.  Run the
focused NC-03/NC-04 tests and all NEWCORE ports, venue and store tests.

## Deferred

The account worker and budget; NC-05 scheduler/locks/read snapshots; Portfolio
or Journal mutation; reconciliation/adoption; sizing; management; simulator;
UI/observability; credentials; PAPER/testnet wiring; REC-02/TNET-01 execution;
mainnet/live funds; and non-Binance venues.
