# BASE-CLEAN-PROOF-02 NEWCORE golden comparison contract

Status: frozen, default-OFF offline collector slice. Base:
`e43b67aa68baf86ea8e5e29d6c826aea1b18bb50`. This slice prepares the
`newcore_golden_adapters` proof. It does not implement either NEWCORE adapter,
change a golden case, or run the comparison pack as part of implementation.

## Roadmap fit and closed admission

This is the smallest independent proof slice for BASE-CLEAN's
`golden_adapters_missing` HOLD. The existing golden schema, blinded case input,
adapter registry, strict comparator, and tier manifest remain authoritative.
The collector adds no second comparison implementation.

An accepted artifact requires the complete Cartesian matrix of the 30 loaded
golden cases and exactly `newcore_sim` plus `newcore_live_sim` (60 pairs). Every
pair must be marked `required`; `pending_adapter`, `known_divergence`,
`not_applicable`, a missing adapter/capability, a missing or duplicate result,
an adapter failure, poison leakage, or any comparator mismatch refuses the
whole artifact. No partial or known-divergence result can satisfy BASE-CLEAN.

The current tree is intentionally refused: 28 cases per NEWCORE adapter are
`pending_adapter`, two per adapter are `known_divergence`, and neither adapter
is registered. That is an open evidence dependency, not a reason to emit a
legacy-only or self-certified proof.

## Deterministic collection

The parent collector verifies a clean caller-pinned Git HEAD before and after
the run. It starts an isolated child process through the golden pack's existing
process-tree deadline. The child receives only `adapters.blind(case)` and
returns canonical trace data; it never receives the expectation or known
divergence ledger. The parent reconstructs each trace and calls the existing
`goldenlib.compare.compare` against the real case.

The accepted body binds:

- schema `zackbot-base-clean-golden-proof/1`, exact Git head, and the fixed
  host-neutral identity `golden_offline`;
- a pack SHA-256 over every sorted `(case id, adapter, case contract SHA-256,
  tier)` pair; and
- every sorted pair plus the SHA-256 of its canonical trace.

The envelope digest is SHA-256 of canonical ASCII JSON for that body. Decode
rejects duplicate fields, unknown fields, wrong types, noncanonical JSON,
altered bodies/digests, and inconsistent pair ordering. Conversion to
BASE-CLEAN reloads the authoritative pack and revalidates the entire matrix
before returning one `GateProof(NEWCORE_GOLDEN_ADAPTERS, ...)` carrying the
same artifact digest. Raw exception text is never retained across the
collector boundary.

## Default-OFF command and acceptance matrix

Nothing imports the collector from `verify.py`, the golden runner, runtime,
PAPER, or strategy code. After both adapters and all 60 strict-required case
bindings are independently accepted on an exact head, the bounded evidence run
is explicit:

```text
python -m tools.base_clean_golden_proof --expected-head <exact-head>
```

It writes once, without overwrite, to
`dev_out/base_clean/golden_adapters_<exact-head>.json`. The child pack has a
120-second hard stop inherited from the authoritative golden harness.

Focused tests cover the current fail-closed state, exact matrix and ordering,
canonical round trips, digest derivation, altered/duplicate/noncanonical data,
strict mismatch and poison refusal, missing/duplicate traces, wrong/dirty or
changed head, timeout/failure sanitization, immutable sealing, proof conversion,
and the absence of automatic runtime/PAPER/network integration.

## Safety boundary and deferred work

This collector reads local Git state and golden fixtures, executes only locally
registered adapters in a killed-on-timeout child, and writes only an ignored
artifact. It has no network, exchange, credential, service/runtime, PAPER, UI,
installer, deletion, or mainnet behavior.

Implementing and independently accepting the two NEWCORE adapters, changing
the 60 case bindings to strict `required`, running and reviewing the exact-head
artifact, and supplying it to the BASE-CLEAN evidence assembly are deferred.
Real Testnet, two-account isolation, runtime promotion, cleanup, PAPER, and all
mainnet actions remain separate gates.
