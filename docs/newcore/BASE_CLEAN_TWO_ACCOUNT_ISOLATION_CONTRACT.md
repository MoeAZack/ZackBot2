# BASE-CLEAN-PROOF-03 two-account isolation contract

Status: frozen offline exact-head proof slice. Base: protected `origin/master`
`e43b67aa68baf86ea8e5e29d6c826aea1b18bb50`. This slice prepares only the
`two_account_isolation` BASE-CLEAN proof. It does not start ZackBot, contact an
exchange, use credentials, or place PAPER, testnet, or mainnet orders.

## Proof boundary

The collector constructs two real NC-04 `AccountContext` and `AccountBudget`
instances. Each account has a different account binding, egress ID, append-only
rate-block journal, fake venue, and injected monotonic clock. All venue answers
are local typed fakes; no network-capable venue or sender is accepted.

Account B queues one protective stop with a fixed 100 ms deadline. While that
work remains queued, account A receives, in order:

1. a sanitized 503 outage observation;
2. a one-shot venue exception, reduced by `AccountContext` to `UNKNOWN`;
3. a 429 with a fixed retry deadline; and
4. a 418 ban that must latch `BLOCKED_UNKNOWN_DURATION` and refuse a later
   protection call without dialing A's venue.

After every A fault, B's full immutable snapshot must equal the snapshot taken
when its protection was queued. B is then advanced by 50 ms; its fake stop call
advances the account-local clock another 25 ms and returns an acknowledged
typed outcome. Completion at 75 ms must be strictly before the 100 ms deadline.
The two journals and venue call traces must contain only their own account's
records and calls. A missing fault, reordered fault, shared egress, late or
unknown B result, cross-account snapshot mutation, or mixed journal/venue
evidence refuses the artifact.

## Exact-head and canonical evidence

`collect_two_account_isolation(expected_head, expected_host_id)` verifies a
clean checkout and the privacy-safe local host identity both before and after
the deterministic run. The artifact contains no account IDs, client IDs,
symbols, order payloads, exception text, URLs, headers, or credentials. It
contains only the exact head, privacy-safe host ID, a digest of the synthetic
bindings, closed typed fault/state values, relative timing, counts, and proof
booleans.

The schema is `zackbot-base-clean-two-account-isolation/1`. Canonical sorted
ASCII JSON and SHA-256 bind the reconstructed immutable body. Duplicate keys,
unknown keys, noncanonical bytes, wrong raw field types, subclasses, forged or
mutated dataclasses, altered digests, dirty/wrong heads, or host changes fail
closed with stable codes and no retained raw exception text. Only a fully
reconstructed accepted artifact converts to
`GateProof(ProofKind.TWO_ACCOUNT_ISOLATION, ...)`.

## Acceptance and deferred work

Focused tests cover the valid fault sequence and deadline, distinct instances
and egress, no cross-account mutation, 429/418 isolation, canonical round trip,
digest binding, redaction, wrong identity, dirty tree, hostile JSON/types,
forged mutable records, deadline/fault/invariant failures, and sanitized
collector exceptions.

Real Binance Testnet execution, credentials, network I/O, runtime/PAPER wiring,
two-account product behavior, strategy behavior, UI, source deletion, proof
publication, BASE-CLEAN promotion, and every mainnet action remain deferred.
