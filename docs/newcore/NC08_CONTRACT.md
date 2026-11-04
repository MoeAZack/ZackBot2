# NC-08 Deterministic Simulator and Replay Contract

Status: freeze-ready draft and contract-test skeleton only. Base: accepted NC-07
exact `de0973d68bef35ae8b020a2d293d4b62245dde60`. Implementation, runtime,
PAPER, UI, and network wiring are not part of this branch.

## Boundary

NC-08 is the deterministic causal replay boundary for live-parity simulation,
historical replay, and backtest evidence. It consumes the real immutable NC-01
`OrderIntent` and NC-07 `ManagementPlan`/action objects. It must not create a
second backtest-only action family, reinterpret purpose/side, or copy action
fields into permissive dictionaries.

NC-08 owns dataset manifests, causal event ordering, path scenarios, simulated
fill application, explicit cost/funding hooks, deterministic result provenance,
and parity evidence. It owns no network, exchange account, credential, clock,
random source, persistence, scheduler, strategy signal, risk authorization,
runtime, PAPER process, UI, or mainnet behavior. NC-03 remains the venue/wire
translator and NC-01 remains the order-ownership authority. NC-08 may prepare
and compare canonical requests; it never sends one.

## Same-object translation and parity

There is one pure translation seam for `LIVE`, `REPLAY`, and `BACKTEST` modes.
Mode selects the execution/fill provider only; it cannot alter action mapping,
rounding, side, quantity, reduce-only meaning, reason, ownership, or protection
replacement order.

- An NC-01 `OrderIntent` enters unchanged and is identified by its NC-01
  canonical bytes/hash.
- An NC-07 plan is consumed with the exact concrete action object from its
  ordered action tuple. Translation also receives an explicit immutable context
  containing account, instrument, side, owner, IDs, evidence time, instrument
  rules, input generations, and any required NC-06 authorization identity.
- `RiskIncreaseProposal` is not executable. It cannot translate to an ADD until
  a fresh NC-06 authorization and the caller-supplied NC-01 identity/context
  exist. Simulation cannot mint or self-approve that authority.
- Protection replacement preserves NC-07 place-before-cancel ordering. A
  simulator cannot mark replacement protection working before the modeled
  acknowledgement/fill evidence permits it.
- The canonical translated request and reason are byte-identical in all three
  modes. Live parity compares translation, inputs, and ordered evidence; it does
  not claim historical fills equal future venue fills.

Acceptance cases: `PARITY-01`, `PARITY-02`, `AUTH-01`.

## Versioned datasets

Every run requires a closed immutable manifest. At minimum it carries schema
version, dataset ID/version, SHA-256 content digest, source class, symbol and
timeframe, inclusive coverage bounds, record count, ordering policy version,
and the exact fill, path, cost, funding, instrument-rule, and translator model
versions/digests. A run result repeats these values.

Dataset bytes are verified before use. Missing, mutated, duplicated,
out-of-order, gapped, mixed-symbol/timeframe, or forming-bar input is rejected
or returns typed `INCOMPLETE`; it is never silently sorted, filled, truncated,
or treated as flat/zero. Replay sees only evidence whose causal availability
time is at or before the decision cursor. Closed-bar consumers retain the
NC-01/BarSource exclusive-close convention. Cross-symbol ties use a supplied
monotonic sequence; timestamps never invent tie order.

Acceptance cases: `DATA-01`, `DATA-02`, `CAUSAL-01`, `REPLAY-02`.

## Path and fill causality

An ordered trade/quote/event path is authoritative only when the manifest and
model version say it is. OHLC alone does not prove intrabar order. If stop and
target, stop and add, or two capacity-affecting actions can occur in more than
one order, NC-08 returns explicit path scenarios or `AMBIGUOUS`; it never chooses
the favorable path, candle-color heuristic, or a hidden default.

An explicit path produces one deterministic event sequence. A fill is generated
only by the versioned fill hook from causally available evidence. The hook
models exact Decimal requested/filled/remaining quantity, partial fills,
liquidity class, price, evidence sequence, and terminal/unknown state. A crossed
price is not itself a fill. Unknown or missing fill evidence remains unresolved
and cannot close ownership. NC-07 receives path/fill facts only after the replay
has applied their ordered evidence.

Acceptance cases: `PATH-01`, `PATH-02`, `FILL-01`, `FILL-02`.

## Costs and funding

Cost, slippage, and funding are injected deterministic hooks with stable model
IDs/versions and exact finite Decimal inputs/outputs. There is no implicit zero,
venue default, current-fee lookup, or float conversion.

- Costs identify maker/taker class, explicit fee rate and amount, slippage rule,
  fill price, quantity, and settlement asset. Partial fills accrue independently.
- Funding uses explicit timestamped rates, position quantity/notional, side,
  settlement convention, and application sequence. It applies only while the
  modeled position is held across the funding event.
- Absent cost coverage, funding coverage, instrument rules, or a required model
  version makes the run `INCOMPLETE`; it cannot produce an acceptance P&L.
- Gross P&L, fees, slippage, funding, and net P&L remain separate exact fields.

Acceptance cases: `COST-01`, `COST-02`, `FUND-01`, `FUND-02`, `DECIMAL-01`.

## Closed deterministic result

The result is a closed immutable typed object with stable run identity, status
(`COMPLETE`, `AMBIGUOUS`, `INCOMPLETE`, or `INVALID`), canonical input/action
hashes, every manifest/model version, ordered events/fills, separate costs and
funding, terminal ownership summary, ambiguity branches, and stable reasons.
Identical canonical inputs return identical canonical result bytes. No raw
exception, mutable reference, callable, file handle, client, or environment
object is retained.

Acceptance cases: `REPLAY-01`, `RESULT-01`, `BOUNDARY-01`, `IMPORT-01`.

## Contract-test skeleton and implementation handoff

`tests/newcore_simulator/test_nc08_contract.py` freezes the case IDs, required
categories, and exact NC-01/NC-07 object families. Implementation cases are
intentionally skipped on this contract-only branch and must be activated, not
deleted or renamed, when `newcore/simulator.py` is authorized. Focused evidence
for this branch proves the matrix itself is closed and complete; it does not
claim a simulator exists.

The smallest implementation slice is one pure module with immutable manifests,
contexts, hooks/protocols, path/fill/result records, canonical serialization,
and a deterministic replay function. Adapters, file readers, reports, runtime,
PAPER and UI remain separate later tickets.

## Codex confirmations before implementation

1. Confirm whether the shared NC-07-action to NC-01-intent identity/context
   injector belongs in NC-08 or in a later integration module. Recommendation:
   NC-08 defines and tests the pure seam; runtime ownership/wiring stays later.
2. Confirm whether an unresolved path returns all deterministic branches or a
   single conservative branch plus `AMBIGUOUS`. Recommendation: preserve all
   branches and forbid a scalar acceptance metric until a preregistered resolver
   selects one.

## Explicit deferral

NC-09 observability, REC-02/TNET-01, dataset acquisition/storage, performance
reports, strategy selection, distributed simulation, runtime, PAPER, UI,
mainnet, credentials, and real-funds behavior are deferred. No later feature may
weaken action-object parity, causal ordering, ambiguity, or explicit cost and
funding completeness.

