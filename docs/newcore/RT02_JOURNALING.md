# RT-02 order journaling and journal projection

**State:** built by Claude Projects and stacked on RT-01 (PR #296, same branch). On 5 November 2026 the owner said to
keep driving the roadmap without handing off to Codex. It has had an adversarial review, a regression test for each
finding, a mutation check and a CP1252 run. Times are Africa/Cairo. PAPER and Binance TESTNET only.

## Roadmap fit

RT-01 left three things for later: a journaling `ActionSink`, a concrete `DurableStateSource`, and a concrete
`EvidencePort`. This slice adds the first two. Both are needed before NEWCORE can run the full vertical slice
without the legacy engine.

## Files

- `newcore/runtime/journaling.py`: `JournalingActionSink`, `SinkBinding`, `SinkProblem`.
- `newcore/runtime/projection.py`: `JournalProjection`.
- `newcore/runtime/runtime.py` has three small changes:
  - it binds a sink that has `bind` before the scheduler exists;
  - guards call `poll` first and turn each problem into a sticky hold;
  - cycle accepts `EntryCandidate`.
- `newcore/runtime/ports.py` gains `EntryCandidate` and an optional `key` argument on `accept_entry`.
- `newcore/ports/journal.py` changes only rule G6 (see "Stop route"). `tests/newcore_ports/journal_contract.py` and
  `test_ports_journal.py` follow that change: a new good flow `algo_first`, and two new refusals,
  `algo_while_previous_live` and `algo_route_for_a_close`.
- Tests: `tests/newcore_runtime/test_rt02_journaling.py`, `test_rt02_projection.py`, `test_rt02_review_fixes.py`
  (one test per review finding) and `test_rt02_guard_lines.py` (one per mutation survivor).

## Stop route (G6 amendment)

- Binance moved conditional orders to the algo service and answers a classic STOP_MARKET with -4120.
- NC-04's table has only the algo `SUBMIT_STOP` row, but rule G6 demanded a classic attempt first, so no stop could
  be journaled and sent.
- G6 now admits an algo protect in three cases:
  - it is the owner's first protect attempt;
  - the previous attempt closed without a rejection (cancelled, expired, filled or not sent);
  - it is the old fallback of a sent classic attempt that closed rejected.
- It still refuses an algo protect while the previous attempt is live or unknown (so stops never double up), after
  an algo rejection (no third route), and for any non-protect intent.
- Journals written under the old rule still replay, because the change only admits more.
- The NC-04 endpoint table and the BASE-CLEAN digests are unchanged.

## Write-ahead order

The sink writes these events on the account's own NC-05 runner thread, one fsync each, in this order:

```
DecisionRecorded -> IntentRecorded (DURABLE) -> IntentStateChanged (SUBMITTED) -> Scheduler.submit
```

- The runner cannot dispatch the work before the hook that queued it returns.
- If any append fails, nothing is queued, and the sink reports `order_journal_failed`.
- If the submit fails after SENT is durable, the order is unresolved, because only a venue query can prove it was
  never sent.
- Before anything is journaled, every payload the action could need is checked against NC-04 `reservation_for`.
  NC-04 refusing a submit would otherwise latch `reconcile_before_restart`.

## Result fold (`poll`, on guard passes)

| NC-04 result / OrderOutcome | Journal | Intent state |
|---|---|---|
| COMPLETED, FINAL with a final status | `ResultObserved` FINAL `exchange_final` | `terminal_for(result)` |
| COMPLETED, REJECTED | FINAL `exchange_refused`, 0 executed | REJECTED |
| COMPLETED, KNOWN with an open status | KNOWN | WORKING |
| NOT_FOUND | UNKNOWN with lookup `not_found` | UNKNOWN, plus `order_unresolved` |
| ACKNOWLEDGED, UNKNOWN, an echo mismatch, an unparseable status, or REFUSED/CANCELLED/EXPIRED/UNKNOWN work | UNKNOWN | UNKNOWN, plus `order_unresolved` |
| No result within twice the order deadline | the work is cancelled if still queued; UNKNOWN | UNKNOWN, plus `order_unresolved` |

The sink never assumes that work NC-04 expired was "not sent". SENT is already durable, and rule G9 forbids
`not_sent` after it.

## Protection

- An entry FINAL with an execution opens `derive_lot_id(account, entry)`. The sink then journals and queues the
  lot's algo stop for the **executed** quantity at the NC-06 request's stop price.
- The sink flattens the rest of the lot with a reduce-only market close (Action FLATTEN, `exit.stop_failed`) and
  reports `protection_failed` in either case:
  - a stop ends while part of the lot is still open (rejected, cancelled, expired or partly executed);
  - NC-04 cannot admit the protect.
- No flatten is journaled on top of a protect whose own append or submit failed.
- A market order (entry or close) answered KNOWN is journaled WORKING and reports `order_unresolved`. Its executed
  quantity is unknown. For an entry, the sink also reports `protection_failed`.
- A result is stamped no earlier than its intent's creation time, so a wall clock that steps back never loses a
  fill. If an entry's result cannot be journaled, the sink reports `protection_failed`.

## Resting-stop watch

The runtime has no user-data stream, so the sink queries every WORKING stop once per order deadline. It does this on
guard passes, through NC-04, and only while no uncertain problem is active.

- **KNOWN `NEW`/`WORKING`:** the stop is still resting.
- **KNOWN with detail `algo_triggered`:** this is what the testnet adapter answers for a triggered algo stop. The
  answer names only the child market order. The sink then reads that child's fills (`FILLS`, at most
  `TRIGGER_READS` = 5 rounds):
  - fills on the stop's symbol and side that sum exactly to the stop quantity are journaled as FINAL `FILLED`, with
    the child id and the fills' VWAP;
  - fills on another symbol or side, over-filling, or no full fill after 5 reads mark the order unresolved and hold
    the account.
- **FINAL:** journaled, and the lot follows (`_after_final`).
- **NOT_FOUND:** `order_unresolved`.
- The result is stamped with the venue's observation time. It is never earlier than the intent and never later than
  the wall clock.

Venue evidence never reads fills by an algo stop's id, because `userTrades` uses a different id space. Between the
venue fill and the sink's journal entry, REC-02 sees the position drop and holds for about one refresh. That hold is
correct and clears once the fill is journaled.

## Entries

An entry is accepted only when all of these hold:
- it comes with a strategy `DecisionKey` (an `EntryCandidate`) whose symbol, side and purpose match the NC-06
  request;
- the signal has not been consumed yet (`claim_signal`);
- nothing is pending and no problem is active;
- NC-04 admits the entry, the fail-safe close and the algo protect.

## Management (NC-07 plans)

- Nothing is routed while the sink's own view is uncertain: `order_unresolved`, `order_journal_failed` or
  `recovered_live_intents`.
- `CloseAction` or `FlattenRequired` for a known lot becomes a CLOSE child intent of no more than the lot's
  quantity, but only when no stop rests. A resting stop would outlive the close, so that case waits for the cancel
  path and holds the account.
- Child decision ids include the journal position. A decision journaled without its intent (a failed append)
  therefore never blocks a retry of that lineage ordinal.
- `ProtectionRepair` becomes a protect child intent on the next G6 route, but only when no stop is live.
- A plan for a lot with a pending order counts as already routed (`True`), and nothing more is sent.
- Every other action (reduce, stop move, cleanup, risk increase), or more than one action, returns `False`, so the
  runtime holds the account (`management_unrouted`).

## Restart

`bind` replays the journal to rebuild lots, including what each stop and close executed, and each lot's last
protect attempt.
- An intent left DURABLE, SUBMITTED, UNKNOWN or CANCELLING raises `recovered_live_intents`, a sticky hold until the
  reconcile query path exists.
- An open lot with no live stop (for example after a crash between the fill and its protect) raises
  `protection_failed`.
- A WORKING stop is normal after a restart. REC-02 checks it against the venue's open orders.

## Projection (`JournalProjection.load_journal`)

The runtime prefers `load_journal(account_id, journal)` over `load`, because the binding is a journaled fact:

- **Account:** taken from the last `BindingChanged`. Only a CONFIRMED binding with its typed confirmation is
  projected; anything else raises `durable_state_invalid`. A mainnet binding projects, but RT-01 refuses it
  (`environment_mismatch`).
- **Positions:** NC-02 booked net quantity per (symbol, side). A negative book raises.
- **Orders:** the live intents. Each carries its last exchange order id and the executed quantity of its FINAL
  result. A KNOWN quantity is never trusted.
- **Generation:** the journal's last sequence.
- **`last_venue_observed_at_ms`:** the latest journaled result time.
- `label` and `hedge_mode` are operator configuration.

## Open items

0. **PAPER run.** `VenueEvidence`, `PaperVenue`, `run`/`init-paper` and the release tool are in
   `docs/newcore/RT02_PAPER_RUN.md`. Evidence reads open orders only for watched symbols, because NC-04 refuses a
   no-symbol read. An order on a symbol nobody configured or traded is therefore not seen, so run NEWCORE on a
   dedicated account.
1. **Venue evidence over the testnet.** *Update:* the adapter now reads the combined classic and algo book for one
   symbol, and the sender allows both paths. The original note follows.
   - `BinanceUsdMTestnetVenue.open_orders` returns UNKNOWN (`split_route_read`) at master, and the sender
     allow-list lacks `/fapi/v1/openOrders`.
   - A concrete `EvidencePort` can therefore prove nothing on the testnet until the classic and algo open-order
     read exists (#291's area). That is the next roadmap step.
2. **Ticket shape.** This slice is stacked on PR #296 on the same branch.

## Review and mutation evidence (non-official, sandbox)

- The adversarial review of ba28b95 confirmed 8 findings, F1 to F8; P2 and P3 were plausible. All are fixed, with
  tests in `test_rt02_review_fixes.py`.
- The mutation check ran 42 mutants on `journaling.py`, `projection.py` and the runtime wiring. 41 were killed.
- The survivor is `lot.quantity <= 0 or not plan.actions` in `accept_management`. It is equivalent: a zero lot
  already fails the quantity checks, and an empty plan fails the one-step rule.

- **Second review (evidence, PAPER venue, stop watch at 67458e4):**
  - three findings were confirmed and are fixed:
    - a triggered algo stop was never journaled under the adapter's answers, and the PAPER venue hid it;
    - the PAPER stop price was reported instead of the fill price;
    - evidence read fills by an algo id;
  - one finding is documented and not fixed: an open order on an unwatched symbol is not seen;
  - one finding is accepted as a correct, transient hold: a brief hold after a stop fill;
  - regression tests are in `test_rt02_review2_fixes.py`.

## Deferred

- The reconcile query path that resolves `order_unresolved` and `recovered_live_intents`.
- Cancel and replace of a live stop (`StopChange`, `ProtectionRepair` with `place_before_cancel`).
- Reduce, add, and more than one action per plan.
- Stop price tick quantization. The NC-06 stop is used as given; a venue rejection ends in the fail-safe flatten.
- The cancel path, which would let a management close (or a stop move) remove the resting stop first.
- The testnet `run` (it needs the testnet open-orders read), and PAPER entries (they need a strategy `EntrySource`).
