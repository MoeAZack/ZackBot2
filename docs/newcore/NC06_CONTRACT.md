# NC-06 Single Risk Gateway Contract

Status: frozen implementation contract. Source: ZackBot2 issue #219 comment
`6618933371`. Base: protected master
`d35665b19823cf411a7af04f853eb9dc68ee245e`.

## Boundary

NC-06 is a pure deterministic Decimal-only gateway for ENTRY and
risk-increasing ADD authorization. It owns no clock, network, store, venue,
worker, strategy, management, order submission, legacy runtime, UI, or PAPER
wiring. REDUCE, CLOSE, and PROTECT do not enter this gateway and can never be
blocked by it; `requires_authorization` makes that boundary explicit.

All inputs are frozen typed snapshots. Every trading number is an exact finite
`Decimal`; integers and floats are refused rather than converted. Account and
environment confirmation, freshness, IDs, generations, policy allocations,
correlation assignments/caps, and verified venue filters are mandatory. There
are no default caps, correlations, filters, prices, leverage, or freshness.

## Capital and simultaneous ceilings

Controlled capital is exactly:

`min(verified equity, configured capital cap) - explicit reserved capital`

The result must be positive. Candidate quantity is the minimum of all of these
post-action ceilings:

- requested risk and per-trade risk;
- portfolio, symbol-side, and strategy/book cumulative risk;
- strategy/book notional allocation;
- account gross and directional net exposure;
- symbol and symbol-side exposure;
- the target symbol's explicitly mapped correlation bucket;
- available margin after in-flight reserved margin, at the lowest requested,
  policy, filter, and follower leverage ceiling;
- venue maximum quantity and notional.

Current exposures and all reserved/in-flight opening authorizations are included
before the candidate is calculated. Hedge-mode LONG and SHORT books remain
separate for symbol-side ceilings; both still count toward gross, symbol, and
correlation exposure. Every represented portfolio symbol must have exactly one
correlation assignment and its bucket must have exactly one cap.

Requested risk is a ceiling. The gateway may reduce quantity or leverage but
never raises risk, leverage, allocation, margin, or controlled capital to make
an order feasible. Entry and stop must be exact tick multiples and have the
correct stop direction. Quantity is rounded down once to the venue step. A
rounded plan below minimum quantity or notional is refused and is never rounded
up. Contradictory minimum/maximum venue filters are refused.

## Decisions and one-shot identity

The result is exactly one closed immutable `ApprovedDecision` or
`RefusedDecision`. Both carry deterministic decision context and all account,
portfolio, policy, and filter generations. An approval additionally carries
exact quantity, notional, risk, margin, leverage, controlled capital, binding
ceilings, and a deterministic `dec_` authorization identity suitable for
NC-01 `OrderIntent.authorized_by`. A refusal has no executable quantity fields.

Decision construction is factory-only. `authorization_is_current` re-evaluates
the complete immutable master input, compares every executable output, checks
all generations, and refuses an identity listed as consumed. Copying or
mutating a decision cannot preserve validity. An existing reservation with the
same request ID refuses as a duplicate, and every reservation participates in
the post-action ceilings.

Equivalent Decimal spellings and identical immutable inputs produce identical
master decisions. Any master generation or executable input change changes or
invalidates the authorization.

## Follower diagnostics

Configured follower profiles are explicit immutable $1,000–$4,000 capital
snapshots with their own generation, freshness, reserved capital/margin,
exposures, risk, allocation use, and leverage ceiling. Each row tests the exact
master quantity with the same ceilings. It is either exact-feasible or reports
stable reasons and bindings; it never proposes a larger or smaller copy.

Follower rows cannot enlarge, refuse, or change the master plan or its
authorization identity. Invalid or stale follower data produces only a
fail-closed diagnostic row. The matrix makes no copy-equivalence claim.

## Acceptance

Focused evidence covers Decimal type and boundary properties, tick/step and
min/max contradictions, every risk/capital/allocation/exposure/correlation/
leverage/margin/venue ceiling, LONG/SHORT hedge isolation, adds and in-flight
reservations, duplicate and stale-generation refusal, no-round-up and monotonic
tighter caps, follower isolation, closed immutable decisions, one-shot
revalidation, deterministic replay, sanitized failures, and import boundaries.
The full NEWCORE suite runs once on the final exact head. Runtime, PAPER, UI,
and mainnet activation remain deferred.

