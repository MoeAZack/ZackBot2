# TNET-01 — injected testnet harness contract

Status: frozen first slice. Roadmap fit: Wave C11, after accepted REC-02. This slice proves a bounded, deterministic orchestration boundary before any exchange or PAPER runtime wiring.

## Ownership and effects

`newcore.testnet_harness` owns one deep interface: validate a frozen suite manifest, derive stable scenario identities, ask an injected adapter for sealed evidence, enforce safety assertions and deadlines, perform idempotent cleanup, and pass one canonical redacted JSON document to an injected writer.

The adapter owns all future effects. This slice contains no network, credential, filesystem, process, git-discovery, clock, sleep, thread, persistence mutation, runtime, or PAPER code. The writer accepts bytes; the kernel does not choose a path or storage system. Adapter and writer failures cross the boundary only as closed stable failure codes, never raw exception text.

## Exact account boundary and hard limits

Every suite is bound to one already-confirmed NC-01 `Account` whose environment is exactly `TESTNET`. The manifest must mark the account dedicated, list one or two unique symbols, and the adapter must reconfirm the exact account ID, venue, settlement asset, key digest, exchange UID, TESTNET environment, and isolation before scenario setup. Position equality is never identity evidence.

The pure kernel refuses manifests or evidence outside these ceilings:

| Limit | Frozen ceiling |
| --- | ---: |
| Symbols | 2 |
| Leverage | 3x |
| Peak notional per scenario | 1,000 USDT |
| Aggregate peak notional | 2,000 USDT |
| Orders per scenario, including cleanup | 12 |
| Scenario duration | 10 minutes |
| Whole suite duration | 60 minutes |

All notional values are finite canonical `Decimal` values. Boolean values never satisfy integer fields. Inputs and adapter outputs must be exact frozen types; the kernel reconstructs them before use so subclasses, forged objects, or mutable caller containers cannot become trusted evidence.

## Frozen scenario matrix

The suite contains exactly these cases, once each and in this order:

1. long entry → protected → target
2. long entry → protected → stop
3. short entry → protected → target
4. short entry → protected → stop
5. protected partial close
6. one bounded DCA add
7. cancel/replace race
8. accepted order whose answer is lost immediately, then reconciled
9. exchange-minimum refusal without an entry
10. protected open position, durable journal reopen, and reconciliation under a newly derived boot identity
11. typed HTTP 429 evidence that blocks non-protection sends, preserves the protection lane, obeys the retry boundary, and resumes no earlier than that boundary
12. typed HTTP 418 evidence that blocks non-protection sends, preserves the protection lane, latches account HOLD, and never resumes inside the scenario

Each case receives deterministic NC-06-style synthetic intent and authorization IDs derived only from the sealed manifest and case. Evidence must bind to those exact IDs and affirm the NC-06 authorization. The lost-answer case requires `answer_lost_at_ms == accepted_at_ms`. The restart case requires an exact newly derived boot ID, durable journal reopen evidence, and reconciliation; the original boot ID cannot be reused. HTTP 429 and 418 are separate closed evidence types; arbitrary status text or an early/self-asserted resume is never accepted.

## Setup, cleanup, and stop policy

Each case starts only after the adapter has reconfirmed the exact binding at or after the suite start and a reconciled flat setup has succeeded. Stale pre-suite confirmation cannot authorize setup or an order. Cleanup is mandatory even when setup or execution fails. The kernel calls cleanup twice with the same deterministic token and accepts it only when both reconstructed results are equal.

Cleanup escalation is frozen:

- protection and reconciliation by 15 seconds;
- flatten by 60 seconds;
- if neither is safely proven, stop new work in `HOLD` by 120 seconds.

`HOLD` is never a passing result. Exactly one terminal timestamp must agree with cleanup final truth: protection, flatten, or HOLD-stop. A flat or protected terminal must be explicitly reconciled; HOLD must not claim reconciliation. Missing, contradictory, unreconciled, late, over-budget, or non-idempotent evidence fails closed, and contradictory/unreconciled terminal evidence is reported as failed `HOLD`. Cleanup orders count toward the scenario's 12-order ceiling.

## Report and privacy boundary

The only accepted output is canonical ASCII JSON (`zackbot-tnet01/1`) with sorted keys and canonical decimal strings. It contains stable IDs, closed statuses/failures, bounded metrics, hashes, and no adapter payloads, endpoint URLs, credentials, raw exception text, or free text. Equivalent sealed evidence produces identical bytes. The injected writer receives those bytes only after the complete report has been reconstructed.

## Deferred

Direct Binance Testnet calls, credential loading, endpoint selection, NC-03 transport, NC-01/02 journal implementation, REC-02 reconciliation wiring, filesystem report persistence, runtime/PAPER scheduling, installer/service behavior, UI, live canaries, and all mainnet activity remain outside TNET-01. A later integration contract must independently prove the adapter and writer implementations without changing these bounds.
