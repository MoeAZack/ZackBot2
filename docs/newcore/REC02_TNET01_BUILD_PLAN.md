# REC-02 / TNET-01 build plan (PRE-STAGE, Claude Code)

Status: **prep only, no code**. Gate text: PR #42 (`codex-roadmap-testnet-gates`, ad4871a), components C10 (REC-02) and C11
(TNET-01), and the new Wave C exit. This document maps every REC-02 matrix row and every TNET-01 scenario to what
already exists, names the exact gap, the owning branch, and whether it can be proven on FakeVenue now or needs the
testnet. It then proposes the REC-02 fold interface, the TNET-01 harness layout and an order that fits the
**21 Oct 2026 (Cairo)** deadline with the **M5 engine gate on 19 Oct**.

Sources read (remote refs, nothing checked out), 2026-10-08:

| Ref | Head | What it holds |
|---|---|---|
| `origin/master` | cb41684 | NC-01 domain + step-0 ports (`newcore/ports/venue.py`, `keys.py`), golden pack |
| `origin/nc-01-domain` | 440bfbd | NC-01 PR #38 (open) |
| `origin/nc-s1-slice` | 34f1668 | Runner (`newcore/runner/*`), FakeVenue, NC-02a store (contains `nc-02a-journal`), hard HOLD, `python -m newcore.run` |
| `origin/nc-venue-testnet` | c872c6b | TestnetVenue, transport, cassettes/scenarios, smoke + smoke_trade, TestnetBarSource, **factory** `newcore.venue.factory:build_testnet` (landed 14:02 today) |
| `origin/nc-02a-journal` | 5e9029c | NC-02a FileJournal + recovery (already inside `nc-s1-slice`) |
| `origin/nc-management` / `nc-management-driver` | 25e34cd / 1890095 | NC-07 management core + pure `ManagementDriver` (driver contains core) |
| `origin/prep/nc02-negative-fixtures` | a6364de | NC-02 acceptance draft r4 (A01-A24, matrix M01-M53), slice plan S1-S7, drills D1-D8 |

Legend for the "Runs on" column: **FV-now** = provable today on FakeVenue (existing hooks), **FV+hook** = FakeVenue after a
small test-only fault hook, **TN** = needs Binance Futures testnet (cassette replay in CI afterwards).

---

## 1. The biggest gaps (read this first)

1. **There is no account-level reconciliation fold.** `Runner.reconcile()` (nc-s1-slice `newcore/runner/runner.py`)
   classifies differences into 8 item kinds and **every** difference becomes a sticky HOLD. It never *resolves* an
   external change: a manual close, a manual add, a venue-only NEWCORE order, a stop that filled with a lost record
   after a corroborated NOT_FOUND. HOLD then needs `resume()`, which needs a clean reconciliation that can never come.
   "Adopt or quarantine" does not exist yet: only *quarantine-by-whole-account-HOLD*.
2. **Fills are never reconciled.** The gate says positions, open orders, **fills** and protection. The Runner reads
   `fills()` only for fees and for an algo child; a FINAL result whose executed qty / VWAP disagree with `userTrades`
   is never detected.
3. **No staleness rule.** `ReadOutcome.observed_at_ms` is never compared with the newest recorded result. A lagging
   `positionRisk` right after a fill reads as a position mismatch and puts the account into a sticky HOLD (false
   positive). On testnet this is likely to fire during TNET-01 and stall the run.
4. **Management is not wired into the Runner.** `ManagementDriver` is pure and tested, but the Runner only does
   entry + initial stop + signal close. Target, partial close, bounded DCA and stop replace (the cancel/replace race)
   cannot run end to end anywhere yet. When wired, `Fold.lots()` would book an ADD fill as a **closing** (it puts every
   non-PROTECT child of a lot into `closes`).
5. **Seam bug that breaks the first testnet cycle:** `AccountReadsShim.funding()` (nc-s1-slice `testnet_hook.py`) reads
   `p.income` / `p.time_ms`, but the real `FundingPayment` (nc-venue-testnet `testnet_venue.py`) has `amount` / `at_ms`.
   The shim test passes only because it builds its own fake `FundingPayment` with the shim's field names. First
   funding read on testnet -> `AttributeError`.
6. **Lost-entry corroboration is cycle-bound.** `_corroborate_entry` needs two agreeing position reads on two
   *different cycles* after `NOT_FOUND_WINDOW_MS` (20 s). On a 4h runner that is >= 8 h of HOLD; TNET-01 needs
   reconcile-only ticks between candles (`cycle(now, decide=False)`), which `cmd_run` does not do.
7. **Client-id reuse is an unverified assumption.** FakeVenue rejects any reused client id (-4116) forever, and the
   same-id resend / emergency-stop idempotence leans on it. Binance documents uniqueness "among open orders"; whether a
   FINAL order's id can be reused on USD-M testnet is **inferred, not measured**. The A23 emergency id is a function of
   (account, symbol, side, qty) only, so a second incident with the same gap would reuse an old id. TNET-01 must probe it.
8. **No TNET harness yet.** `smoke_trade.run_trade_smoke` proves one LONG-only min-size round trip through the port, not
   through the Runner. The `run` CLI accepts only `1h`/`4h` and the one real strategy, so no scenario can be driven on
   demand, and it loads instrument rules from a static file instead of the factory's live `instrument_rules`.
9. **Testnet account isolation (slice-plan O1) is still open.** A NEWCORE run against the legacy bot's non-flat testnet
   account goes straight to HOLD. TNET-01 needs a dedicated testnet key stored with `tools/newcore_keys.py` (owner
   action: credentials are protected).

---

## 2. What exists today (inventory used in the matrix)

| Component | File (branch) | Role in REC-02 / TNET-01 |
|---|---|---|
| Runner cycle | `newcore/runner/runner.py` (s1) | sync (query every live intent by client id) -> reconcile -> protect -> decide -> reconcile + invariants I1-I3 |
| Fold | `newcore/runner/fold.py` (s1) | journal -> intents, lots, mode; restart = fold(journal) |
| Reconcile v0 | `Runner.reconcile()` (s1) | items: position, foreign_order, orphan_order, order_mismatch, stop_missing, ambiguous, unreadable, emergency_stop |
| Lost-answer path | `Runner._sync/_resolve/_resend/_corroborate_entry` (s1) | query by client id; same-id resend for reduce-only; entry never resent; NOT_FOUND_CORROBORATED / POSITION_ADOPTED |
| Hard HOLD (A23/A24) | `Runner._emergency_set/_drain/_emergency_stop/_handover_emergency` (s1) | store-down emergency stop + drain + handover |
| FakeVenue | `newcore/adapters/fake_venue.py` (s1) | hooks: `lose_next_market_answer`, `not_found`, `unknown_reads`, `rest_next_entries`, `fill_resting`, `fill_when_cancelled`, `lose_next_cancel_answer`, `refuse_classic_stops`, `external_cancel`, `inject_position`; `to_state/from_state` for restarts |
| NC-02a store | `newcore/store/*` (s1, from nc-02a-journal) | FileJournal, recovery verdicts, durability HOLD |
| TestnetVenue | `newcore/venue/testnet_venue.py` (venue) | port mapping; duplicate id -> UNKNOWN `duplicate_client_id`; `algo_route`; `algo_triggered` child id |
| Factory | `newcore/venue/factory.py` (venue) | creds from DPAPI store, binding digest check, clock, hedge check, rules |
| Cassette scenarios | `newcore/venue/scenarios.py` (venue) | LIFECYCLE, ALGO_ROUTE, DUPLICATE, NOT_FOUND_THEN_FINAL, HEDGE_MODE (scripted HTTP, replayed) |
| Owner smoke | `newcore/venue/smoke_trade.py`, `tools/newcore_smoke.py` (venue) | one LONG min-size round trip; exit codes 0-8; sanitized cassette under `%LOCALAPPDATA%\ZackBotNC\cassettes` |
| Management | `newcore/management/{core,driver}.py` (driver) | pure targets, TP1 partial, BE, trail, time exit, one ADD, replace-then-cancel |
| Redaction | `newcore/venue/redact.py`, `CassetteRecorder` (venue) | by-name + by-value scrub, fail-closed leak audit |

Test files named below: `tests/newcore_slice/*` (s1), `tests/newcore_venue/*` (venue), `tests/newcore_management/*`
(driver). `::` separates file and test.

---

## 3. REC-02 matrix (C10)

Owning branches: **s1** = `nc-s1-slice` (Runner owner, Claude); **rec** = proposed `nc-rec02` (new, off `nc-s1-slice`,
Claude; new files under `newcore/reconcile/` + the `Runner.reconcile()` call site only); **venue** = `nc-venue-testnet`
(transport lane); **mgmt** = `nc-management-driver`; **NC-01 amend** = Codex ruling on the frozen contract.

| # | Row | (a) Existing coverage | (b) Exact gap | (c) Owner | (d) Runs on |
|---|---|---|---|---|---|
| R01 | **Local-only order: ENTRY** sent, venue has no record | `test_runner_e2e::test_not_found_alone_is_still_unknown`, `::test_not_found_with_nothing_executed_is_unknown_until_corroborated_and_never_resent`, `test_crash_safety::test_3_entry_send_crash_resolves_by_corroboration_then_resume_succeeds`, `::test_3b_a_lost_entry_that_did_fill_is_adopted_and_protected` | Corroboration needs 2 reads on 2 cycles (>= 8 h at 4h). The fold must accept reconcile-only ticks (`decide=False`) at a bounded cadence (proposal: every 30 s until resolved, max 10 min, then HOLD item with owner action). Corroboration uses `positions()` only; also check `userTrades` in the send window when an exchange order id becomes known. | rec | FV-now (logic), TN (timing) |
| R02 | **Local-only order: PROTECT / CLOSE** (reduce-only) unknown to the venue | `test_crash_safety::test_1_stop_send_crash_is_resent_with_the_same_client_id_never_active_naked`, `::test_2_close_send_crash_after_the_stop_cancel_completes_the_close`, `test_algo_fallback::test_crash_around_the_algo_send_never_sends_the_route_twice` | Same-id resend is safe only if a FINAL id cannot be re-accepted (gap 7). If Binance re-accepts a used id, a resent CLOSE after an unseen FINAL could reduce a *different* lot on the same side once adds exist. Needs probe P1 and, if reuse is allowed, a fills check before resend. | rec + venue (probe) | FV-now, TN (P1) |
| R03 | **Local-only position**: journal has an open lot, venue side is flat (manual close, or stop filled with the record lost) | `test_runner_e2e::test_reconciliation_mismatch_holds_and_stops_entries` (opposite direction only) | Becomes `position` item -> sticky HOLD, never resolved. Need: read `userTrades` for that symbol/side since the lot opened; fills of an **owned** order id -> book that intent's FINAL (A19, M10); fills of no owned order -> external close: book with evidence, release the stale stop (cancel owned reduce-only order on a flat side), end FLAT. NC-01 has no evidence kind and no action for an external close. | rec + **NC-01 amend** | FV+hook (`external_close`) |
| R04 | **Venue-only order, foreign** client id | none named (code path: `foreign_order` item) | Correct per A22 (never touched), but it HOLDs the whole account. Proposal: quarantine per (symbol, side) so other symbols keep trading, with owner actions `leave` / `cancel`. Codex ruling needed (A22 vs per-symbol quarantine). Add a test. | rec + Codex | FV+hook (`inject_order`) |
| R05 | **Venue-only order with a NEWCORE id** that is not in the journal (journal behind, other portfolio, rolled back) | none (classified as `foreign_order`) | Must be its own class `unjournaled_newcore` with evidence (`route_of`, shape, side, reduce flag) and owner actions; a reduce-only one on a flat side may be auto-cancelled (owned-shape, cannot add exposure). Emergency ids: `test_hard_hold_a24::test_store_back_hands_over_to_journaled_protection_then_resume`. | rec | FV+hook |
| R06 | **Venue-only position, foreign** | `test_runner_e2e::test_reconciliation_mismatch_holds_and_stops_entries` | HOLD is right; missing: actionable evidence (qty, entry price, which owner actions are possible: adopt-as-lot with a stop at X, close, exclude symbol) and an operator ADOPT path. NC-01 has `Action.RECONCILE` but no adopt decision/lot source for a foreign position. | rec + NC-01 amend | FV-now |
| R07 | **Partial fill** (entry FINAL with executed < requested; KNOWN `PARTIALLY_FILLED`) | `test_hard_hold_a24::test_m50_partial_fill_before_the_cancel_is_adopted_and_protected_exactly` (hard HOLD only), `test_ncv_testnet_venue::test_final_expired_partial_keeps_executed` (mapping) | No normal-mode Runner test for a partial FINAL entry (lot = executed, stop = executed). `_follow_child` returns None forever when an algo stop's child fills partially (qty != intent qty), so the reduction is never booked and the account HOLDs on `position`. Needs a KNOWN-partial / child-terminal rule. | s1 | FV+hook (`partial_next_market`, `partial_child`) |
| R08 | **Late fill** (UNKNOWN then FINAL later) | `test_runner_e2e::test_unknown_entry_answer_holds_then_the_query_resolves_it`, `test_golden_s1::test_lost_entry_answer_golden_resolves_by_query_into_one_lot`, cassette `NOT_FOUND_THEN_FINAL` | Missing: a fill that appears **after** the entry was resolved `not_found_corroborated` (terminal). Today it is a `position` surplus -> HOLD with no link back to the intent. Need a re-open rule: a FINAL record for an owned client id overrides a corroborated decision via a new RECONCILE decision (A19: exchange evidence wins). Also late fill of a cancelled resting entry outside hard HOLD (M51 normal mode). | rec + NC-01 amend (re-open) | FV+hook |
| R09 | **Missing order** (owned stop vanished: cancelled/expired outside the bot) | `test_runner_e2e::test_external_stop_cancel_is_restored` | Covered for classic. Add algo-route vanish and "vanished while the account is in HOLD" (protect runs in HOLD, but add the test). | s1 | FV-now |
| R10 | **Duplicate order** | `test_crash_safety::test_5_duplicate_client_id_means_the_order_exists`, `test_testnet_semantics::test_1_duplicate_client_id_as_unknown_is_read_back_not_refused`, cassette `DUPLICATE`, I2 in `check_invariants`, `test_durability_and_keys::test_duplicate_candle_and_redelivered_entry_signal_create_one_entry` | Two owned live reduce-only orders for one lot (superseded stop left after a replace, or classic + algo both resting) -> `orphan_order` -> HOLD with no action. Rule: an owned reduce-only orphan whose replacement is confirmed WORKING, or whose side is flat, is cancelled (A23 order: new confirmed first). Client-id reuse semantics: probe P1. | rec (+mgmt for replace) | FV+hook (`duplicate_stop`), TN (P1) |
| R11 | **Manual close** (full or partial) | none | Full = R03. Partial: position < lot qty, stop qty > position. Need: book the external reduction (R03 rule), then **resize** the stop to the remaining qty (replace-then-cancel, NC-01 `test_nc01_protection_resize`), end PROTECTED. | rec + mgmt | FV+hook (`external_reduce`) |
| R12 | **Manual add** (position > owned) | `test_runner_e2e::test_reconciliation_mismatch_holds_and_stops_entries` (from flat only) | Surplus is HOLD + I1 incident but stays **unprotected** in normal HOLD (the emergency set only runs in hard HOLD). Proposal: protect the surplus reduce-only at the lot's stop price with a deterministic id, never adopt it into the lot, quarantine until the owner adopts/closes. Conflicts with A22 "never auto-handled": needs a Codex ruling (protect-only is not adoption). | rec + Codex | FV-now |
| R13 | **Stop filled while offline** | Code path `_sync` (query stop -> FINAL; algo: `_follow_child`); `test_testnet_semantics::test_3_triggered_algo_stop_is_booked_from_the_child_fills` (online) | No test that advances the venue with the Runner stopped and then restarts (classic and algo). G-OUTAGE-STOP-L-01 is an S3 golden not yet flipped (`golden_adapter` raises NotExpressible for outage). | s1 | FV-now (`advance_to` while stopped + `restart`) |
| R14 | **Stale answer** | none | No freshness rule. Need: (i) a read older than the newest recorded result for that symbol is re-read, never compared; (ii) a diff exactly explained by a FINAL recorded in this cycle is re-read once after a settle delay before it can HOLD; (iii) a query answer must never regress an intent (KNOWN after FINAL is ignored, it is already guarded by phase but untested). Settle delay from probe P2. | rec | FV+hook (`lag_positions(n)`), TN (P2) |
| R15 | **Unknown status** (UNKNOWN / ACKNOWLEDGED / unreadable reads / `algo_triggered_no_child`) | `test_runner_e2e::test_unknown_entry_answer_holds_then_the_query_resolves_it`, `test_algo_fallback::test_no_fallback_after_an_unknown_until_it_is_resolved`, `::test_an_unresolvable_unknown_never_falls_back`, `test_hard_hold_a24::test_m49_unknown_cancel_is_requeried_and_recancelled_only_while_working` | A HOLD whose only reasons were `unreadable` / `ambiguous` stays HOLD after the reads recover. Proposal (A08-compatible): auto-promotion by a RECONCILE decision when the binding is confirmed, the candidate is non-trivial and a fresh full match holds; otherwise owner RESUME. Codex ruling. | rec + Codex | FV-now |
| R16 | **Restart** | `test_runner_e2e::test_restart_mid_entry_cycle_no_duplicate_entry_or_stop`, `::test_restart_mid_exit_cycle_finishes_the_close_without_a_fresh_stop`, `::test_restart_mid_trade_rebuilds_from_the_journal`, `test_crash_safety::test_crash_between_journal_and_venue`, `test_hard_hold_a24::test_m53_restart_mid_drain_repeats_from_exchange_truth_idempotently`, `test_book_s4::test_restart_with_three_lots_open_rebuilds_the_book`, `test_run_cli::test_restart_resumes_from_the_journal_with_identical_outputs` | Restart is strong against an **unchanged** venue. Missing: restart combined with R03/R11/R12/R13 (venue changed while down) and a STARTUP-trigger reconciliation that runs before the first decision. Boot verdict DURABILITY_UNAVAILABLE exits 3 instead of entering hard HOLD (`app.py` notes it as an NC-02a gap). | rec + s1 | FV-now |
| R17 | **Fill truth** (FINAL result vs `userTrades`) | none (fees only, `Runner._fee`) | Compare sum(fills.qty) == executed_qty and VWAP == avg_price (exact Decimal) for every FINAL with executed > 0, once; mismatch -> `fill_mismatch` HOLD item. `fills()` returns UNKNOWN on a full page (never partial). | rec | FV+hook (`skew_fill`), TN |
| R18 | **Protection truth** (stop qty/price differs, under-cover) | I1 in `check_invariants` (counted, HOLD); `order_mismatch` item | No named test for `order_mismatch` or for under-cover after a partial. Rule: owned stop mismatch -> replace-then-cancel to the journaled intent, else HOLD. | rec + mgmt | FV+hook |
| R19 | **Never guess an empty account** | `Runner.portfolio()` raises unless a fresh flat snapshot; `check_flat_snapshot_fresh`; NC-02 A06/A09 | First run against a non-flat account HOLDs but writes no INIT/HOLD-INIT record (A09). Add the STARTUP verdict `HOLD_INIT` with per-item evidence. | rec | FV-now |

**Exit condition per row (the gate's sentence):** every test ends with `Verdict.outcome in {FLAT, PROTECTED, HOLD}`;
HOLD carries >= 1 item with `owner_action`, and I1 holds (no naked quantity) unless the item is an explicit
`UNPROTECTABLE` with evidence (no stop level derivable).

---

## 4. TNET-01 scenarios (C11)

All scenarios run the **Runner** (not `smoke_trade`) with the factory's parts, a FileJournal under
`%LOCALAPPDATA%\ZackBotNC\tnet\<run id>`, `ScriptedSignals` on **1m** candles, SOLUSDT or another core-8 symbol with a
small min notional, minimum feasible size, hedge mode. Price cannot be forced on testnet: stop/target fills use
**tight brackets plus a bounded wait** (section 6.3), and anything that did not happen inside its bound is reported
`INCONCLUSIVE` (retried up to N attempts), never PASS.

| # | Scenario | (a) Existing coverage | (b) Exact gap | (c) Owner | (d) Runs on |
|---|---|---|---|---|---|
| T01 | **Complete long cycle** (entry -> stop WORKING -> close -> flat, fills/fees booked) | `test_runner_e2e::test_entry_fill_protect_signal_close` (FakeVenue), `smoke_trade.run_trade_smoke` + `test_ncv_smoke_trade::test_trade_round_trip_happy_path` (port only, fake HTTP), cassette `LIFECYCLE` | Runner never driven against TestnetVenue. Blockers: funding shim bug (gap 5), static rules in `app.rules_for`, `run` CLI only 1h/4h + one strategy, no reconcile-only ticks. | s1 (shim, rules) + tnet | TN |
| T02 | **Complete short cycle** | `test_runner_e2e::test_entry_fill_protect_signal_close[short]` (FakeVenue) | `smoke_trade` is LONG-only; no short ever sent to testnet. | tnet | TN |
| T03 | **Confirmed entry** (FINAL executed qty/VWAP == userTrades) | fees read in `smoke_trade` step 9 | R17 fill-truth check missing. | rec | TN |
| T04 | **Stop exit** (classic and algo; algo child fills) | `test_testnet_semantics::test_3_triggered_algo_stop_is_booked_from_the_child_fills`, cassette `ALGO_ROUTE` (shapes only) | Never observed live. Bracket method: stop at ~k x 1m-ATR, wait bounded. Which route testnet actually requires is unmeasured (smoke defaults to algo). | tnet | TN |
| T05 | **Target exit** (bot-side trigger -> MARKET REDUCE/CLOSE) | `test_driver::test_end_to_end_with_add_held_partial_tp1_net_be_and_time_exit` (pure) | Driver not wired into the Runner; no per-cycle mark feed for `on_mark` (1m kline close is enough for TNET); targets are bot-side (slice O4). | mgmt -> s1 integration | FV after wiring, then TN |
| T06 | **Partial close** (TP1 REDUCE, stop resized replace-then-cancel) | driver test above; NC-01 `test_nc01_protection_resize` | Wiring + fold must keep REDUCE closings separate and resize protection; reconcile must tolerate the replace window (old + new both WORKING, coverage >= position). | mgmt + rec | FV after wiring, then TN |
| T07 | **One bounded DCA** (ADD held until a confirmed stop covers; add fills; stop replaced to cover) | driver test above; goldens `G-GAP-DCA-ONEADD-L-01` / `-S-01` (master); `test_durability_and_keys::test_a_keyed_add_is_consumed_once_also_across_a_restart` (decision only) | Runner has no ADD execution; `Fold.lots()` would book ADD fills as closings; entry gate `CAPACITY_IN_TRADE` must not block a keyed ADD; cap = exactly one add. | mgmt + s1 (fold fix) | FV after wiring, then TN |
| T08 | **Cancel/replace race** | FakeVenue `fill_when_cancelled`, `lose_next_cancel_answer`; `test_hard_hold_a24::test_m51_fill_after_the_cancel_wins_and_is_protected`, `::test_m49_...` (hard HOLD drain only) | No stop replace in normal mode yet (needs driver). Testnet cannot force a fill race; force the **cancel-side** race instead: the harness cancels the old stop through its own transport ("operator hand") right before the Runner's cancel -> the Runner sees -2011 REJECTED -> query -> FINAL CANCELED, coverage never below position. Fill-wins race stays FakeVenue-only. | mgmt + tnet | FV-now (fill race), TN (cancel race) |
| T09 | **Lost answer + reconciliation** | cassette `NOT_FOUND_THEN_FINAL`; `test_runner_e2e::test_unknown_entry_answer_holds_then_the_query_resolves_it` | Needs a `LoseAnswerHttp` wrapper around `TestnetHttpSender` (send the request, drop the response, raise `WireTimeout`), injected through `build_testnet(http=...)`. Resolution then by query or corroboration (R01 cadence). | tnet | TN |
| T10 | **Refusal / minimum failure** | `test_ncv_testnet_venue::test_rejected_carries_the_code_and_one_request_only`, `test_ncv_smoke_trade::test_entry_refused_stops_clean`, `test_book_s4::test_leverage_cap_trims_then_refuses_on_shared_equity`, `test_crash_safety::test_4_stop_and_close_both_refused_is_bounded_never_recursion` | (a) sizing floor -> SKIP, nothing sent: needs a scripted signal whose risk sizes below `market_min_qty`; (b) venue refusal (-4164 notional / -1111 precision): needs a **TNET-only** raw-qty override (refused by config outside the harness profile); assert FINAL `exchange_refused`, no lot, no HOLD. (c) stop refusal -2021 stays FakeVenue. | tnet (+ s1 hook) | TN (a, b), FV-now (c) |
| T11 | **Restart with an open position** | `test_runner_e2e::test_restart_mid_trade_rebuilds_from_the_journal`, `test_run_cli::test_restart_resumes_from_the_journal_with_identical_outputs` | Real process restart on testnet: harness runs the Runner as a subprocess, kills it after the stop is WORKING, waits >= 1 candle, restarts on the same FileJournal; STARTUP reconcile clean; position closes normally. | tnet | TN |
| T12 | **Final exchange truth** | `smoke_trade` flat check | Harness-owned final verdict + cleanup guarantee (section 6.4). | tnet | TN |
| P1 | **Probe: client-id reuse** | none (FakeVenue assumes never reusable) | Place a far stop with id X, cancel it, resubmit X: accepted or -4116? Then a min-size open with id Y (filled), resubmit Y. Result recorded in the report; if reuse is allowed, R02 and the A23 emergency id need a nonce. | tnet | TN |
| P2 | **Probe: read lag after a fill** | none | Time from entry FINAL to `positionRisk` showing it, and from algo trigger to child visible. Sets the R14 settle delay. | tnet | TN |

---

## 5. REC-02 fold interface (proposal)

Pure, deterministic, no IO, no clock: the Runner gathers reads, the fold decides, the Runner journals and acts through
its existing journal-first paths. Same shape for replay (FakeVenue) and testnet.

```python
# newcore/reconcile/__init__.py  (new package; imports newcore.domain + newcore.ports only)

class Trigger(enum.StrEnum):
    STARTUP = 'startup'                  # first reconciliation after a restart, BEFORE any decision
    CYCLE = 'cycle'                      # start / end of every cycle
    TICK = 'tick'                        # reconcile-only tick between candles (decide=False)
    TIMEOUT = 'timeout'                  # a venue call timed out
    UNCERTAIN = 'uncertain'              # UNKNOWN / ACKNOWLEDGED / NOT_FOUND answer recorded
    EXTERNAL = 'external'                # a diff the journal cannot explain (manual change suspected)
    OPERATOR = 'operator'                # RESUME / ADOPT request

@dataclass(frozen=True, slots=True, kw_only=True)
class VenueTruth:                        # one account snapshot; every read keeps its kind (UNKNOWN is never empty)
    at_ms: int
    positions: ReadOutcome               # VenuePosition tuple
    orders: ReadOutcome                  # VenueOrder tuple, classic + algo (partial read = UNKNOWN)
    fills: Mapping[str, ReadOutcome]     # exchange order id -> VenueFill tuple (only ids in ReadPlan.fills)
    trades_since: Mapping[tuple, ReadOutcome]   # (symbol, side) -> fills since ms (external-change rows only)

@dataclass(frozen=True, slots=True, kw_only=True)
class ReadPlan:                          # bounded: what the fold needs before it can decide
    positions: bool
    orders: bool
    fills: tuple[str, ...]               # FINAL results not yet fill-checked (R17)
    trades_since: tuple[tuple[str, str, int], ...]   # (symbol, side, from_ms) for R03 / R11 / R13
    settle_ms: int | None                # re-read after this delay before any HOLD (R14)

class ItemKind(enum.StrEnum):
    LOCAL_ONLY_ENTRY = 'local_only_entry'        # R01
    LOCAL_ONLY_REDUCE = 'local_only_reduce'      # R02
    LOCAL_ONLY_POSITION = 'local_only_position'  # R03 / R11 (deficit)
    FOREIGN_ORDER = 'foreign_order'              # R04
    UNJOURNALED_NEWCORE = 'unjournaled_newcore'  # R05
    EMERGENCY_ORDER = 'emergency_order'          # A23 handover
    FOREIGN_POSITION = 'foreign_position'        # R06 / R12 (surplus)
    PARTIAL = 'partial'                          # R07
    LATE_FILL = 'late_fill'                      # R08
    STOP_MISSING = 'stop_missing'                # R09
    ORPHAN_OWNED = 'orphan_owned'                # R10
    ORDER_MISMATCH = 'order_mismatch'            # R18
    UNDER_PROTECTED = 'under_protected'          # I1
    FILL_MISMATCH = 'fill_mismatch'              # R17
    STALE_READ = 'stale_read'                    # R14
    UNREADABLE = 'unreadable'                    # R15
    AMBIGUOUS = 'ambiguous'                      # R15

class Resolution(enum.StrEnum):          # what the Runner does with an item (journal first, then venue)
    BOOK_RESULT = 'book_result'          # an evidence-backed OrderResult for an owned intent (A19)
    BOOK_EXTERNAL = 'book_external'      # NC-01 amendment: external close/add recorded with venue evidence
    RESEND_SAME_ID = 'resend_same_id'    # reduce-only, never an entry
    RESTORE_STOP = 'restore_stop'
    RESIZE_STOP = 'resize_stop'          # replace-then-cancel
    CANCEL_OWNED = 'cancel_owned'        # owned reduce-only orphan, replacement confirmed or side flat
    PROTECT_SURPLUS = 'protect_surplus'  # Codex ruling (R12)
    REREAD = 'reread'                    # bounded by ReadPlan.settle_ms
    QUARANTINE = 'quarantine'            # HOLD item, owner action required
    NONE = 'none'

@dataclass(frozen=True, slots=True, kw_only=True)
class Item:
    kind: ItemKind
    symbol: str | None
    side: str | None
    client_id: str | None
    intent_id: str | None
    local_qty: Decimal | None
    venue_qty: Decimal | None
    evidence: tuple[str, ...]            # stable tokens + ids (trade ids, order ids, read times), never raw bodies
    resolution: Resolution
    owner_actions: tuple[str, ...]       # non-empty when resolution is QUARANTINE: 'adopt_lot@<stop>', 'close', ...

class Outcome(enum.StrEnum):
    FLAT = 'flat'
    PROTECTED = 'protected'
    HOLD = 'hold'

@dataclass(frozen=True, slots=True, kw_only=True)
class Verdict:
    reconciliation_id: str
    at_ms: int
    trigger: Trigger
    outcome: Outcome
    ownership: Ownership                 # KNOWN / KNOWN_EMPTY / UNKNOWN (never guessed)
    items: tuple[Item, ...]              # sorted: protect > book > cancel > reread > quarantine
    hold_reasons: tuple[ReasonCode, ...]
    scope: tuple[tuple[str, str], ...]   # (symbol, side) under quarantine; () = whole account (Codex ruling R04)

def plan_reads(view: AccountView, trigger: Trigger, *, now_ms: int, policy: RecPolicy) -> ReadPlan: ...
def reconcile(view: AccountView, truth: VenueTruth, *, trigger: Trigger, now_ms: int, policy: RecPolicy) -> Verdict: ...
```

`AccountView` is a read-only projection of the Runner `Fold` (lots, live intents, results, mode, last reconciliation)
so the fold never sees the journal object. `RecPolicy` holds the numbers: settle delay (from P2), tick cadence and cap
for R01, fill tolerance (exact), and whether R04/R12 quarantine is per side or account-wide.

Properties the tests must hold for every row: (1) pure: same `(view, truth)` -> identical Verdict bytes;
(2) monotone: applying the Verdict's actions and re-reconciling a venue that did not change gives no new action;
(3) outcome is FLAT only if the snapshot is fresh, OK and flat, PROTECTED only if I1 holds, otherwise HOLD with
owner actions; (4) no Resolution increases exposure; (5) an UNKNOWN read never yields FLAT or a booked result.
Runner integration: `Runner.reconcile()` becomes `plan_reads` -> reads -> `reconcile` -> journal a RECONCILE decision
(+ results for BOOK_*) -> act. The existing 8 item kinds map 1:1 onto the new ones, so current tests stay as they are.

---

## 6. TNET-01 harness layout (proposal)

### 6.1 Files

```
tools/newcore_tnet.py                 CLI: plan (default, no network) | run | report | cleanup
newcore/tnet/__init__.py
newcore/tnet/spec.py                  Scenario / Step / Expect / Bound dataclasses; TNET_SCENARIOS registry
newcore/tnet/scenarios.py             T01-T12 + P1/P2 as data (long/short mirrored by a parameter)
newcore/tnet/signals.py               ScriptedSignals: fire ENTER/CLOSE on the next CLOSED 1m candle (keyed, unique run nonce)
newcore/tnet/seams.py                 LoseAnswerHttp, OperatorHand (direct transport calls = "manual exchange change")
newcore/tnet/driver.py                runs the Runner (in-process or subprocess for T11) with reconcile-only ticks
newcore/tnet/cleanup.py               the cleanup guarantee (6.4)
newcore/tnet/report.py                redacted exact-build report (6.5)
tests/newcore_tnet/                   every scenario against FakeVenue + cassette replay of real runs (CI, no network)
```

The harness builds parts with `newcore.venue.factory.build_testnet(config, http=...)`, uses the factory's
`instrument_rules` (not the static file) and constructs `Runner` directly with a TNET `RunnerConfig`; it does not go
through `python -m newcore.run` (whose config accepts only `1h`/`4h` and `trend_ema_mom.v1`). Client ids stay unique
per run because `ScriptedSignals.name = 'tnet_' + 8 hex run nonce` (fits `[a-z0-9_]{1,24}`) and the journal is fresh
per run. Everything runs on the testnet host only (the transport is hard-pinned); the account must be a dedicated
NEWCORE testnet account (O1), checked at preflight.

### 6.2 Scenario spec

```python
@dataclass(frozen=True)
class Bound:
    max_wall_s: int                       # whole scenario
    max_cycles: int
    max_orders: int                       # hard cap on submits (runaway guard)
    max_notional_usdt: Decimal            # per scenario, checked before every submit by the harness venue wrapper

@dataclass(frozen=True)
class Scenario:
    id: str                               # 'T01-long', 'T02-short', ...
    side: str                             # LONG / SHORT
    needs: tuple[str, ...]                # capabilities: 'management', 'add', 'lose_answer', 'operator_hand', 'restart'
    steps: tuple[Step, ...]               # signal(at=next_close, action, stop_k, target_k) | tick | lose_next(op)
                                          # | operator(cancel=leg | close=qty | add=qty) | restart(after=...) | wait_until(pred, max_s)
    expect: tuple[Expect, ...]            # journal predicates (results, evidence, lots), venue predicates, counters
    final: str                            # 'flat' | 'protected_reconciled'
    bound: Bound
    attempts: int = 1                     # bracket scenarios: retry up to N while INCONCLUSIVE
```

Every scenario is first proven on FakeVenue in CI (same spec, FakeVenue + its hooks), then run on testnet; a testnet run
records a sanitized cassette that CI replays.

### 6.3 Making stop / target happen on testnet

Bracket method: entry at market; stop at `k_stop x ATR(1m, 14)` and target trigger at `k_tgt x ATR`, small `k`
(proposal 1.0 / 1.0); wait up to `max_wall_s` (proposal 20 min). Whichever leg fires is asserted; the scenario repeats
(mirrored side alternating) until both a stop exit and a target exit have been observed for each side, bounded by
`attempts` (proposal 6). Missing legs are reported `INCONCLUSIVE`, which fails the gate run but is not a safety failure.

### 6.4 Cleanup guarantee

1. **Preflight refuses** unless: hedge mode, the account is flat with no open classic/algo orders (or every order
   carries this harness's run nonce prefix from a previous crashed run, see 3), binding digest matches, testnet host.
2. **Every submit goes through a harness wrapper** that enforces `Bound.max_orders` / `max_notional_usdt` and records
   the client id in an append-only run ledger (`%LOCALAPPDATA%\ZackBotNC\tnet\<run id>\ledger.jsonl`) **before** the
   send.
3. **Teardown runs in `finally` and on SIGINT/SIGTERM/SIGBREAK** and also as a standalone `tools/newcore_tnet.py
   cleanup --run <id>` for a crashed run: (a) cancel every open order whose client id is in the ledger (query to
   confirm FINAL); (b) close every non-zero position on the scenario symbols with a reduce-only market order under a
   deterministic cleanup id; (c) re-read positions + open orders twice, >= P2 settle apart; (d) result = `FLAT` or
   `RESIDUE` with the exact residue listed.
4. A position or order **not in the ledger** is never touched (A22): it fails preflight, or is reported as residue.
5. Exit code 8 (below) is the only outcome that can leave exposure, and it prints the client ids to check.

### 6.5 Redacted exact-build report

One JSON file plus a short text summary under `%LOCALAPPDATA%\ZackBotNC\tnet\<run id>\`, never in the repo:

```json
{
  "schema": "zackbot.newcore.tnet_report/1",
  "build": {"git_sha": "<40 hex>", "dirty": false, "branch": "<name>", "python": "3.x.y",
            "files_sha256": {"newcore/runner/runner.py": "...", "...": "..."}},
  "run": {"id": "tnet_1a2b3c4d", "started_cairo": "2026-10-16 14:05", "started_utc_ms": 0,
          "venue": "binance_usdm_testnet", "account_ref": "acct_<hash>", "binding_digest": "<16 hex>",
          "symbols": ["SOLUSDT"], "tf": "1m"},
  "probes": {"P1_client_id_reuse": {"cancelled_stop_id": "accepted|-4116", "filled_order_id": "accepted|-4116"},
             "P2_lag_ms": {"position_after_fill_p50": 0, "p95": 0, "algo_child_visible_p95": 0}},
  "scenarios": [{"id": "T01-long", "verdict": "PASS|FAIL|INCONCLUSIVE|SKIPPED", "attempts": 1, "wall_s": 0,
                 "orders": [{"intent": "int_..", "client_id": "zbn1o-..", "purpose": "entry", "kind": "final",
                             "executed": "0.04", "avg": "151.2", "fills_match": true}],
                 "assertions": [{"name": "stop_working_before_next_cycle", "ok": true}],
                 "counters": {"unprotected_cycles": 0, "holds": 0, "resends": 0, "incidents": 0},
                 "final_truth": {"outcome": "flat", "reconciliation_id": "rec_.."}}],
  "cleanup": {"outcome": "FLAT|RESIDUE", "residue": []},
  "gate": {"rec02_matrix": "<sha of the FakeVenue matrix result on the same build>", "pass": false}
}
```

Redaction: the report and its cassette pass through `newcore.venue.redact` (by name + registered key/secret values) and
the `CassetteRecorder.to_json` leak audit; a leak deletes the file and exits 5. Kept: client ids, intent ids, exchange
order/trade ids, quantities, prices, fees, times. Dropped: keys, signatures, listen keys, raw headers/bodies, balances
beyond the scenario's realised P&L and fees. Times are Cairo first, UTC ms beside. `build.dirty = true` refuses
`--gate` (an exact-build report must come from a committed head).

### 6.6 Exit codes (aligned with `tools/newcore_smoke.py` 0-8 where the meaning matches)

| Code | Meaning |
|---|---|
| 0 | every selected scenario PASS, cleanup FLAT, report written and leak-checked |
| 1 | completed, cleanup FLAT, at least one scenario INCONCLUSIVE (bounded wait elapsed); safety intact |
| 2 | usage / config refused (also: dirty tree with `--gate`, non-testnet host) |
| 3 | no usable credentials / binding mismatch (owner runs `tools/newcore_keys.py`) |
| 4 | preflight refused: account not flat / not hedge / foreign orders present / reads not OK |
| 5 | report or cassette not written, or a leak was found (file removed) |
| 6 | the run deadline was exceeded (cleanup ran, FLAT) |
| 7 | at least one scenario FAIL (assertion or safety counter), cleanup FLAT |
| 8 | cleanup RESIDUE: exposure or an order of this run **may be left**; client ids printed |

---

## 7. FakeVenue hooks to add (test-only, nc-s1-slice / nc-rec02)

`external_close(symbol, side, qty, price)` (manual close / reduce), `inject_order(ref, ...)` (foreign or unjournaled
NEWCORE order), `partial_next_market(frac)`, `partial_child(client_id, qty)`, `lag_positions(n_reads)`,
`skew_fill(eoid, qty|price)`, `duplicate_stop(client_id)`, and a `reuse_ids` switch that models the P1 outcome if
Binance accepts reused ids. Each stays off by default, like the existing hooks.

---

## 8. Order that fits 21 Oct (Cairo)

Milestones from the controlling plan: M1 NC-01/02 frozen 9 Oct, M2 slice runnable 12-13 Oct, M3 long+short trend after
costs 15 Oct, M4 range/scalp partial + bounded DCA 17 Oct, **M5 engine gate 19 Oct**, M6 buffer 20-21 Oct. The Wave C
exit now requires REC-02 + TNET-01 before any strategy is enabled, so both must finish inside M5.

| Date (Cairo) | Build (Claude) | Integration (Codex) | Evidence (Cowork) |
|---|---|---|---|
| **Thu 8 Oct** | This plan. Fix the funding shim (gap 5) on nc-s1-slice (+ a test built from the real `FundingPayment`). | Rule Q1-Q5 below. | Review this matrix; start FakeVenue hook specs. |
| **Fri 9 Oct** (M1) | `nc-rec02` off nc-s1-slice: `newcore/reconcile/` fold skeleton + 1:1 mapping of today's 8 items; rows R09, R13, R15, R16, R19 green (FV-now). | Accept NC-01; record file independence nc-rec02 vs nc-s1-slice (`runner.py` touched only at `reconcile()`). | Matrix harness for rows on the frozen spec. |
| **Sat 10 - Sun 11 Oct** | FakeVenue hooks (section 7); rows R01, R02, R05, R07, R08, R14, R17, R18 green. | Review nc-rec02 r1. | Independent attack on R03/R08/R14 (the evidence rules). |
| **Mon 12 Oct** (M2 fake) | Merge nc-venue-testnet into the slice integration branch (single owner); TNET skeleton: spec, ScriptedSignals 1m, cleanup, report, exit codes; T01/T02/T10a/T12 on FakeVenue. | Rule NC-01 amendment for external close / re-open (Q2). | Build the TNET report checker. |
| **Tue 13 Oct** (M2 testnet) | **First testnet run** (owner key stored, O1 account): P1, P2, T01, T02, T03, T09, T10, T11, T12. Rows R03, R11 implemented per Q2. | Review the first report. | Re-run the cassettes in CI; diff vs FakeVenue. |
| **Wed 14 - Thu 15 Oct** (M3) | Wire `ManagementDriver` into the Runner + fold fix for ADD/REDUCE; T04 (bracket), T05, T06 on FakeVenue then testnet; R12 per Q3. | Review the wiring. | Short/long mirror checks on the report. |
| **Fri 16 - Sat 17 Oct** (M4) | T07 (one bounded DCA), T08 (cancel race on testnet, fill race on FakeVenue); full REC-02 matrix green; full TNET run #1 on the candidate head. | Pre-gate review. | Independent full TNET run #2 on the same head. |
| **Sun 18 Oct** | Fix only; freeze the head. | Accept the head. | Final matrix + TNET reruns, sign-off evidence. |
| **Mon 19 Oct** (M5) | Engine gate pack: REC-02 matrix result + TNET-01 exact-build report on the accepted head. | Gate decision. | Gate evidence. |
| **20-21 Oct** (M6) | Buffer: INCONCLUSIVE reruns, residue fixes. | | |

Critical path: Q2 (NC-01 external-close evidence) -> R03/R11 -> T05-T07 need the management wiring, which is the
largest single item. If the wiring slips past 15 Oct, run T01-T04 + T09-T12 for the gate and mark T05-T07 AT RISK
(they block only the M4 range/scalp strategy, not the trend long/short mechanics).

Schedule health today: **AT RISK** - blockers: O1 dedicated testnet account + stored key (owner), Q2 ruling, management
wiring not started in the Runner. Recovery: everything up to 12 Oct is FakeVenue-only and can proceed now.

---

## 9. Questions for Codex (recommendation first)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Quarantine scope for a foreign order / position (R04, R06): whole account or per (symbol, side)? | Per (symbol, side) HOLD; entries on other symbols continue; the item lists owner actions. |
| Q2 | How is an external close / external add booked (R03, R11) and how is a corroborated NOT_FOUND re-opened by a late fill (R08)? | A RECONCILE decision plus a post-hoc REDUCE/CLOSE intent with a new Evidence `exchange_external` (venue trade ids as corroboration); a FINAL record of an owned client id always supersedes `not_found_corroborated` via a new RECONCILE decision. NC-01 amendment. |
| Q3 | May HOLD protect a manual-add surplus (R12) reduce-only without adopting it? | Yes: protect-only is not "handling" under A22; deterministic id; never adopted into a lot without an owner decision. |
| Q4 | Auto-leave HOLD when the only reasons were unreadable/ambiguous and a fresh full match now holds (R15)? | Yes under A08 conditions (binding confirmed, non-trivial candidate, fresh match), recorded as a RECONCILE decision; every other HOLD needs the owner. |
| Q5 | If P1 shows Binance re-accepts a FINAL order's client id: add a nonce to emergency ids and require a fills check before any same-id resend? | Yes; decide after P1 is measured (13 Oct). |

LANES: Build - Claude Code: funding-shim fix, then `nc-rec02` fold skeleton (R09/R13/R15/R16/R19). Evidence - Cowork:
review this matrix and draft the FakeVenue hook specs + TNET report checker. Integration - Codex: rule Q1-Q5 and
record nc-rec02 / nc-s1-slice file independence.
