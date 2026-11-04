# NC-09 Structured Observability Contract

Status: frozen pure-kernel contract and bounded implementation. Base: accepted
NC-08 exact `226211f9b726d804862f6ec109b607ab27c433b5`. There is no runtime, PAPER,
UI, persistence, file, network, or writer wiring in this branch.

## Authority and safety boundary

NC-09 observes NEWCORE facts; it never creates, changes, approves, retries, or
suppresses a decision, intent, protection action, fill, reconciliation result,
or management transition. The real NC-01 `Decision`, `Incident`, `Fill`, and
reason registry, NC-07 `ManagementPlan`, and NC-08 fill/path results remain the
authoritative objects. NC-09 must not introduce permissive copies of those
families or infer authority from display text.

Every decision observation carries the exact decision/plan identity and stable typed reason,
plus policy/version, causal evidence references or bindings, subject,
account, symbol/side when present, observation sequence, and canonical source
hash. Free text is display-only and can never replace a reason or provenance.
Missing reason/provenance makes an observation invalid; it is not recorded as
`unknown` with invented defaults.

Observability is downstream of safety. Admission, queue saturation, encoding,
writer failure, shutdown, or a broken/absent sink must never delay or alter
PROTECT, FLATTEN, CLOSE, REDUCE, reconciliation, or any exchange action. An
observability call returns a small typed admission outcome and retains no raw
exception. Safety work is never retried because audit work failed.

Acceptance cases: `INC-01`, `INC-02`, `DEC-01`, `DEC-02`, `PROTECT-01`.

## Structured incidents and decision explanations

Structured incidents reuse the exact NC-01 `Incident` and `IncidentRecorded`
families. Account, kind/reason, affected record references, evidence, time, and
sanitized bounded detail remain typed. Raw venue payloads, exception text,
credentials, configuration, request headers, URLs, and arbitrary dictionaries
are forbidden.

why-held and why-closed records are causal observations, not retrospective
stories. They identify the exact selected NC-01 `ReasonCode` or NC-07
`PlanReason`, source identity/hash, evaluated-at sequence/time, evidence and
input generations. A HOLD/no-action observation remains distinct from a close;
an absent close is never labelled as a successful hold. When facts are stale,
ambiguous, incomplete, or generation-mismatched, the stable source reason is
retained and numeric conclusions that depend on missing facts remain null.

Acceptance cases: `WHY-01`, `WHY-02`, `PRIVACY-01`.

## Fill and slippage observations

Fill observations bind requested, known filled, and remaining quantities;
expected/reference price; actual known average/fill price; maker/taker class;
fee/cost model identity; source result/decision; and exact ordered evidence.
Every trading value is a finite `Decimal`. Partial, unknown, not-found, and
terminal outcomes remain distinct. Requested quantity, a crossed price, or a
later position snapshot cannot be substituted for an unknown execution.

Slippage is separately reported in price, settlement amount, and basis points,
with an explicit sign convention and reference-price provenance. Unknown price
or quantity yields null slippage plus typed incomplete coverage, never zero.
Long/short and entry/exit direction are explicit; no quantity sign or prose is
used to infer them.

Acceptance cases: `FILL-01`, `SLIP-01`, `ORDER-01`.

## Excursion and lifecycle economics

MFE and MAE use causally available ordered observations only. Every observation
binds mark, quantity, average entry, side, time/sequence, risk basis, cost model,
and coverage state. Price excursion and lifecycle economics are separate:

- price MFE/MAE records favorable/adverse price, amount, percent, R, and time;
- lifecycle peak records realized P&L plus open P&L less known fees, slippage,
  funding, and modeled exit cost;
- peak give-back is lifecycle peak net minus final net, with percent reported
  only when its frozen denominator is positive;
- adds, partial exits, fee/funding events, and average-entry changes create new
  explicit state snapshots and never rewrite an earlier peak's basis;
- a sampling/outage gap is explicit coverage loss. Candle extremes or future
  evidence cannot fill it, and incomplete economics cannot become an
  acceptance metric.

All comparisons and arithmetic are deterministic finite `Decimal`; timestamps
never break ties without the explicit sequence. Long and short behavior is
symmetric under the stated side convention.

Acceptance cases: `MFE-01`, `MAE-01`, `PEAK-01`, `GIVEBACK-01`, `COVERAGE-01`.

## Bounded admission and writer accounting

The producer boundary is a bounded, non-waiting admission operation. Capacity
is explicit and positive. It never performs file/network I/O, calls a sink, or
waits for a writer. Closed/full/unavailable admission returns a typed drop
reason immediately. It does not evict or reorder an already accepted record.

Counters distinguish at least attempted, accepted, persisted, dropped-full,
dropped-closed, invalid, write-errors, and currently queued. Only a successful
writer acknowledgement advances persisted summaries. A failed write advances a
write-error counter, never fabricates persistence or analytics, and cannot make
the producer retry a trading action. Queue/accounting snapshots are immutable,
bounded, sanitized, and generation/sequence-labelled. Stop/flush and durable
writer ownership belong to a later adapter and must have bounded deadlines;
this pure contract does not create a thread or open a file.

Acceptance cases: `QUEUE-01`, `QUEUE-02`, `QUEUE-03`, `WRITE-01`, `WRITE-02`,
`RESULT-01`.

## Determinism and boundary

Canonical event/result bytes include schema/type, stable reason, full source
provenance, model/policy versions, exact Decimal values, ordered evidence, and
coverage. Equal canonical inputs produce equal bytes and identity. Records are
closed immutable typed values; no mutable reference, callable, client, raw
exception, environment object, secret, file handle, or thread is retained.

The pure NC-09 implementation may depend only on stdlib value/arithmetic
modules and accepted NEWCORE domain/management/simulator types. It owns no
clock, scheduler, store, filesystem, network, exchange, runtime, PAPER, UI,
credential, or mainnet behavior, and imports no legacy module.

Acceptance cases: `BOUNDARY-01`, `IMPORT-01`.

## Contract tests and smallest later implementation

`tests/newcore_observability/test_nc09_contract.py` freezes and activates 24 case
IDs against the exact upstream object families and `newcore/observability.py`.
No case was deleted or renamed when the Codex rulings were frozen.

The bounded implementation is one pure `newcore/observability.py` value
and transition kernel: immutable envelopes, metrics, queue state/outcomes,
writer acknowledgements, counters, canonical bytes, and sanitized failures.
Actual writer threads, persistence adapters, dashboards and runtime hooks remain
separate integration work.

## Frozen Codex rulings

1. **Incident durability:** safety/control incidents are durably journaled by
   NC-01/NC-02 first. NC-09 is only a best-effort droppable mirror and is never
   the sole audit authority.
2. **Excursion basis:** use ordered mark/event samples only and explicit gaps.
   Price excursion and lifecycle net are separate series. Each sample freezes
   contemporaneous average entry/quantity and lot-generation risk-basis ID.
   MFE/MAE percent uses absolute contemporaneous average entry; R uses that
   generation's accepted risk basis. Adds/partials begin a new explicit segment
   and never rewrite earlier peaks. Give-back percent exists only for a positive
   frozen lifecycle peak net; otherwise it is null.
3. **Queue policy:** default capacity is 4096 per account stream and the hard
   maximum is 65536; callers may choose 1..65536. Saturation drops the incoming
   record, never evicts/reorders accepted records, and counts drops by kind and
   severity. Critical incidents remain durable through NC-01/NC-02.
4. **Multiple explanations:** the selected typed stable reason is mandatory.
   Alternatives are optional ordered typed tuples with their own bindings,
   never free text or a recomputed ranking.

## Explicit deferral

REC-02 integrated reconciliation, TNET-01 scenarios, runtime adapters, writer
threads/files/databases, rotation/retention, restart restoration, alerts,
dashboards, UI/API/Telegram, performance reporting, strategy selection, PAPER,
mainnet, credentials, and real-funds behavior are deferred. These later layers
may consume NC-09 records but may not weaken provenance, causal coverage,
bounded admission, or the protection non-blocking invariant.
