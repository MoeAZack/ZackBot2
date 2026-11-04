# NC-07 Management Transition Contract

Status: frozen implementation contract. Source: ZackBot2 issue #219 comment
`6619751707`. Base: accepted NC-06 exact
`8786d9247c8b892c6a44c0aeacff03402bf35779`.

## Boundary

NC-07 is a pure deterministic Decimal-only transition planner for one position
lot. It owns no clock, network, persistence, fills, IDs, retry, sleep,
market-data fetch, strategy signal generation, order submission, runtime, UI,
or PAPER wiring. It consumes frozen lot, policy, market, protection, strategy
exit, and pending-intent facts whose generations and freshness are explicit.
All price, quantity, and risk values must be exact finite `Decimal` instances;
floats and integers are not converted.

The result is one factory-only immutable `ManagementPlan`. It carries a stable
plan identity, lot and policy identity, every input generation, one reason,
bindings, and one ordered immutable action tuple. Malformed, stale, mismatched,
or arithmetically unrepresentable facts produce a sanitized HOLD/RECONCILE
plan. No exception text, callable, live object, or mutable reference is retained.

## Frozen precedence

Each evaluation applies exactly this priority:

1. repair missing or undersized protection;
2. confirmed forced or versioned strategy close;
3. due target/partial reductions;
4. breakeven stop improvement;
5. trailing stop improvement;
6. completed-bar time exit;
7. bounded DCA or pyramid proposal.

A close suppresses reductions and adds; a reduction suppresses adds. An
existing pending intent prevents a duplicate transition. An explicit close may
still supersede another pending kind and records that conflict as a binding.
Unknown pending state reconciles rather than guessing.

## Protection and causal observations

Stop replacements are monotonic toward less risk: a long stop never moves down
and a short stop never moves up. Long candidates round down to the venue tick;
short candidates round up. Every replacement is place-before-cancel and is not
treated as confirmed until a later generation reports WORKING. Missing and
undersized protection is repaired before discretionary management. Unknown
protection forbids additions; if price is already beyond the initial stop, the
plan is RECONCILE/FLATTEN_REQUIRED rather than a synthetic stop fill.

Target, breakeven, trail, and risk-increase triggers use only an explicitly
completed causal market observation. Each target level is identified and may
execute once. Reductions are rounded down to the quantity step, never exceed
current quantity, and preserve the configured positive runner. A bar that
touches both stop and target without an ordered path returns AMBIGUOUS/HOLD for
NC-08. A stop touch is never promoted into a fill; STOP_FIRST without confirmed
exit requests reconciliation. Working target identities are explicit protection
facts; a crossed working target also reconciles until its fill state is known,
instead of emitting a duplicate reduction.

Time exits use only the supplied completed-bars-held count. Strategy exits are
explicit, versioned facts. Replaying identical inputs returns the identical
plan and identity.

## Risk-increase handoff

DCA and pyramid rules have exact level identities, non-increasing requested
risk, maximum per-add risk, count, and cumulative-risk ceilings. A proposal is
never produced after an exit, target reduction, pending intent, or protection
uncertainty. `RiskIncreaseProposal` deliberately has no quantity field and
states `requires_nc06_authorization=True`; it cannot become an ADD intent until
fresh NC-06 authorization succeeds.

## Acceptance and deferrals

Focused evidence covers long/short symmetry, tick-monotonic stops, target and
runner conservation, repeated-call identity, unordered stop/target ambiguity,
gap reconciliation, precedence, pending conflicts, bounded DCA/pyramid
proposals, stale generations, Decimal extremes, immutable closed outputs, and
stdlib-only imports. Full NEWCORE runs once on the final exact head.

NC-08 path/fill simulation, NC-09 observability, REC-02/TNET-01, runtime, UI,
PAPER, and mainnet activation remain deferred.
