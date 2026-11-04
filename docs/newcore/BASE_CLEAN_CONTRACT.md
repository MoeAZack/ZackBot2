# BASE-CLEAN Checkpoint C contract

Status: frozen pure-gate/register first slice. Base: accepted TNET-01 exact
`69793a83f2504ea5fe566d83d2d05f173bebb16c`. This is canonical Checkpoint C
`BASE-CLEAN`, not NC-12 and not runtime activation.

## Roadmap fit and seam

BASE-CLEAN sits after NC-01–09, REC-02 and TNET-01 and before Wave D runtime
promotion. Its one interface is:

```text
evaluate_base_clean(register, evidence) -> GateResult
```

The caller discovers tracked paths and collects exact-head evidence. The pure
module reconstructs immutable values, classifies the supplied universe, checks
the frozen proof set and returns only `ELIGIBLE` or typed `HOLD`. It performs no
filesystem scan, git/process call, deletion, packaging, network, clock, runtime,
PAPER, credential or mainnet operation.

`ELIGIBLE` means the Wave C evidence permits a separately reviewed promotion
step. It never installs, starts, activates or trades. A missing, failed, stale,
mixed-head, forged or over-budget proof returns `HOLD`.

## Authoritative path register

The universe is every tracked path supplied by the external `git ls-files`
adapter. Every path must match exactly one register rule and exactly one of:

- `DELETE`
- `RESEARCH_ONLY`
- `TEMPORARY_ADAPTER`
- `STILL_REQUIRED`

Evidence is not a fifth class. Contracts, tests, schemas, ledgers and review
records use `STILL_REQUIRED` with the typed `contract_evidence` role. Temporary
adapters require a named consumer and expiry gate. A delete row cannot retain a
consumer. Missing and multiple classifications both HOLD.

The current register truthfully keeps `engine.py`, `grid.py` and `ai_filter.py`
as `STILL_REQUIRED/runtime_legacy`: the shipped entrypoint imports them. Each is
also marked `target_delete_no_port` until a replacement entrypoint and zero
consumers are proven. The mark does not authorize deletion. The two legacy
golden adapters are temporary comparison adapters; the remaining golden pack is
still-required evidence.

## Required proof set

All command and proof evidence names the evaluated exact head. The authoritative
pack is:

1. `python verify.py full`
2. `python tests/newcore/nc01_mutations.py`
3. `python tests/newcore_store/nc02a_mutations.py`

The two mutation scripts remain separate until their checks are integrated into
the full plan. Eligibility also requires accepted NEWCORE golden adapters, a
real exact-build TNET report, two-account isolation, and a current-host codec
rebaseline. The present expected result is HOLD because those four proofs do not
yet exist.

## Frozen numeric evidence

All measurements are finite canonical `Decimal` values bound to one host ID and
the exact head.

| Evidence | Ceiling |
| --- | ---: |
| NC-01 validation | 1,000 us |
| NC-01 encode | 6,750 us |
| NC-01 decode | 23,400 us |
| Scheduler submit | 10 ms |
| Scheduler owner step | 50 ms |
| Snapshot mean | 1 ms |
| Publication | 250 ms |
| Private working set | 512 MiB provisional |
| Growth over steady run | 32 MiB provisional |
| Memory run | at least 60 minutes |

The old codec baseline used another Python version; an exact current-host
baseline is mandatory even when supplied values are below the ceilings. Memory
limits are explicitly provisional evidence requirements, not performance claims.

## NC-04 request budgets

Admission ceilings must be parsed from authoritative exchange-info and account
order-limit evidence. No numeric venue ceiling may be supplied from defaults or
caller configuration. Evidence binds the accepted NC-04 endpoint table version,
documented endpoint costs, time windows and reserve formula:

```text
max(minimum, ceil(verified_limit * 0.10)), capped at verified_limit
```

The exact-head proof must affirm both authoritative sources, no hardcoded
ceiling, verified windows/costs, the exact NC-04 cost-table digest
`66e65242d60d23a8d07ecb99c17f4b449b28ce6b8ed64b5c05b248475937fa90`,
and the frozen policy digest. BASE-CLEAN does not recalculate or replace NC-04
admission.

## Determinism and hostile inputs

Inputs and nested records must be exact frozen types. The evaluator rebuilds
them, requires sorted unique paths and unique commands/proofs, canonicalizes all
bytes, and derives one stable decision identity. Subclasses, booleans in integer
fields, mutable containers, malformed paths, non-finite decimals, duplicate or
overlapping classifications, wrong-head evidence and altered policy ceilings
fail closed.

## Deferred

Filesystem/git discovery adapter, NEWCORE golden adapters, real Binance Testnet
execution, source deletion, replacement entrypoint, installer/runtime wiring,
PAPER activation, performance optimization, strategy behavior, UI migration,
release/private-repository work and every mainnet action are outside this slice.
