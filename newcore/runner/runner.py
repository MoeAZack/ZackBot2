"""The S1 Runner: ONE loop, used for replay now and for live later (only the injected ports differ).

Ports: JournalPort (MemoryJournal now, NC-02a later), VenuePort (FakeVenue now, TestnetVenue later), BarSource
(CsvBarSource now, klines later), a SignalSource, and AccountReads (equity + funding; not in step 0, see below).

cycle(now_ms), called once per closed candle (now_ms = that candle's close = the next candle's open):
  1. sync        query every sent, non-terminal owned intent by client id; record what changed (FINAL / KNOWN /
                 UNKNOWN / NOT_FOUND). A durable never-sent intent is sent (protect / close) or, for an entry outside its
                 candle window, closed NOT_SENT. Emergency set (runs in HOLD too):
                   - a REDUCE-ONLY intent (stop / close) that is UNKNOWN + NOT_FOUND is re-sent under the SAME client
                     id (the crash fell between 'sent' and the venue call, or the answer was lost and it never
                     landed). Idempotent: a duplicate-id refusal means the order exists and is read by its client id.
                   - an ENTRY that is UNKNOWN + NOT_FOUND is never assumed unfilled and never re-sent: >= 2 agreeing
                     position reads after NOT_FOUND_WINDOW_MS + a RECONCILE decision resolve it (not_found_corroborated
                     or position_adopted); until then HOLD.
  2. reconcile   fresh venue positions + open orders vs the fold. Any difference -> HOLD (durable ModeChanged); new
                 entries stop. Nothing owned + a flat snapshot is the only way to KNOWN_EMPTY.
  3. protect     every open lot without a live stop (and no close in flight) is SECURED with bounded attempts: a stop;
                 if the venue refuses it, a reduce-only close; at most SECURE_ROUNDS rounds per lot per cycle (never
                 recursion), then a durable HOLD + incident. Runs in HOLD too.
  4. decide      strategy signals on the closed candles: CLOSE first (cancel the stop, reduce-only market close), then
                 ENTER (decision -> sizing -> intent -> market -> result -> stop intent -> stop -> result).
  5. reconcile + invariants (end of cycle).
Journal order for every order: decision_recorded -> intent_recorded (DURABLE) -> sent -> [venue call] ->
result_recorded -> state change / intent_closed. Nothing is sent before its intent is durable; nothing is applied before
its result is durable.

Consumed signal (STEP0 section 2): a DecisionKey is consumed by its decision_recorded (ENTER, SKIP alike). A re-delivered
signal (restart, re-run cycle, replay) finds its decision with JournalPort.find_decision and creates nothing new. The one
exception is the crash gap "decision recorded, intent not": the entry's derived intent (from the recorded decision) is
recorded and sent only while the cycle is still the signal candle's (now_ms == candle_close_ms); later it is spent.

Invariants checked at the end of every cycle. A naked position (I1) outside HOLD first moves to a durable HOLD with an
incident (never ACTIVE while naked), then raises InvariantBreach when the config is strict (tests); counted in HOLD:
  I1 protection  every venue position is covered by NC-01 confirmed_coverage (WORKING carriers only) of its lots,
                 each carrier listed on the venue now; the covered quantity
                 equals the position. Bounded window: exposure may exist without a confirmed stop only INSIDE one cycle
                 (between the entry's FINAL result and the stop's KNOWN result; between the stop cancel and the close
                 result), i.e. for zero candle-path time in replay and one cycle of venue calls live.
  I2 identity    no client id recorded twice; at most one ENTRY intent per entry DecisionKey; every listed owned order's
                 client id maps to exactly one intent.
  I3 domain      the NC-01 Portfolio built from the fold constructs (all its cross-record invariants) and passes
                 check_account_portfolio; KNOWN_EMPTY also passes check_flat_snapshot_fresh at this cycle.
HOLD is sticky: only `resume()` (an operator RESUME decision + a clean reconciliation) leaves it.

AccountReads (equity(), funding(symbol, side, from, to)) is not part of step 0 (section 7 defers equity / income); it
is duck-typed here and reported as an interface gap. A JournalUnavailable (store failure) puts the process in a hard HOLD:
no decision, send or journal write until a restart; cycles only reconcile and count the invariants (S3 adds the
emergency protection set).
Runner boundary (Codex ruling): ENTER and ADD decisions are keyed (keys.decision_key) or refused (UnkeyedOpeningDecision).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from decimal import Decimal

from newcore.domain import (Account, Action, Authority, Decision, DecisionRecorded, EntriesMode, HoldKind,
                            InstrumentRules, IntentRecorded, IntentState, IntentStateChanged, Lot, LotSource,
                            ModeChanged, Op, Ownership, OwnershipProof, Portfolio, Position, ProofKind, Protection,
                            Purpose, ReasonCode, ResultObserved, ResultPhase, Rounding, Side, check_account_portfolio,
                            check_flat_snapshot_fresh, confirmed_coverage, permitted)
from newcore.domain.modes import Permission
from newcore.domain import Evidence, Lookup, OrderResult
from newcore.domain.orders import NOT_FOUND_WINDOW_MS, REDUCE_ONLY, PositionRead, terminal_for
from newcore.domain.portfolio import Fill
from newcore.ports.journal import JournalUnavailable
from newcore.store.hold import durability_hold, hard_hold_permits
from newcore.ports.keys import check_decision_key, route_of
from newcore.ports.venue import MarketOrder, OrderRef, OutcomeKind, ReadKind, StopOrder

from . import ids
from .fold import Fold, OPEN_STATES
from .outcome import summarize, trade_outcome
from .records import not_sent_result, planned_intent, result_from
from .signals import CLOSE, ENTER
from .sizing import SizingPolicy, size_entry

ZERO = Decimal(0)
GATE_REASON = {EntriesMode.HOLD: ReasonCode.RECONCILE_UNRECONCILED, EntriesMode.PAUSED: ReasonCode.FILTER_PAUSED,
               EntriesMode.HALTED: ReasonCode.FILTER_HALT, EntriesMode.FLATTENING: ReasonCode.FILTER_PAUSED}
ITEM_REASON = {'position': ReasonCode.OWNERSHIP_UNTRACKED_POSITION, 'foreign_order': ReasonCode.OWNERSHIP_FOREIGN_ORDER,
               'orphan_order': ReasonCode.LIFECYCLE_ORPHAN_CANCEL, 'order_mismatch': ReasonCode.PROTECT_OWNER_CHECK,
               'stop_missing': ReasonCode.PROTECT_CHECKING, 'ambiguous': ReasonCode.EXEC_ENTRY_UNCONFIRMED,
               'unreadable': ReasonCode.CONNECTIVITY_EXCHANGE_OUTAGE, 'emergency_stop': ReasonCode.PROTECT_RESTORING}


OPENING_ACTIONS = {Action.ENTER: Purpose.ENTRY, Action.ADD: Purpose.ADD}
DUPLICATE_CLIENT_ID = -4116           # Binance "ClientOrderId is duplicated": the order EXISTS (step-0 has no kind for it)
ALGO_FALLBACK_CODES = (-4120, -1116, -1102, -4136)   # newcore.venue.errors (transport): use the algo service
SECURE_ROUNDS = 2                     # per lot per cycle: stop attempt, then (if refused) one reduce-only close
OPENING_PURPOSES = frozenset({Purpose.ENTRY, Purpose.ADD})


class UnkeyedOpeningDecision(ValueError):
    """Runner boundary (Codex ruling): an ENTER or ADD is only ever a keyed strategy decision (keys.decision_key:
    candle + timeframe identity), so a duplicate candle / re-delivered signal is consumed once. Never an unkeyed one."""


class InvariantBreach(AssertionError):
    """A per-cycle safety invariant failed outside HOLD: the run stops."""


@dataclass(frozen=True)
class RunnerConfig:
    account: Account
    portfolio_id: str
    symbols: tuple
    tf_ms: int
    timeframe: str
    rules: dict                                # symbol -> InstrumentRules
    sizing: SizingPolicy = SizingPolicy()
    sides: tuple = ('LONG', 'SHORT')
    slot_id: str = 'S1'
    policy_version: str = 'nc-s1'
    strict: bool = True                        # raise InvariantBreach outside HOLD


@dataclass(frozen=True)
class Reconciliation:
    at_ms: int
    reconciliation_id: str
    items: tuple                               # ((kind, detail), ...): empty = match
    positions: tuple | None
    orders: tuple | None

    @property
    def ok(self):
        return not self.items

    @property
    def flat(self):
        return self.positions is not None and self.orders is not None and \
            all(p.qty == 0 for p in self.positions) and not self.orders


@dataclass
class Counters:
    cycles: int = 0
    entries: int = 0
    skips: int = 0
    redelivered: int = 0
    holds: int = 0
    mismatch_cycles: int = 0
    unprotected_cycles: int = 0
    reconciliations: int = 0
    hard_holds: int = 0
    hard_hold_cycles: int = 0
    resends: int = 0
    incidents: int = 0
    cap_exceeded: int = 0
    drains: int = 0
    emergency_stops: int = 0


class Runner:
    def __init__(self, config: RunnerConfig, *, journal, venue, bars, signals, account_reads=None, hard_hold=None):
        self.cfg = config
        self.acct = config.account.account_id
        self.pf = config.portfolio_id
        self.journal, self.venue, self.bars, self.signals = journal, venue, bars, signals
        self.reads = account_reads if account_reads is not None else venue
        self.fold = Fold.replay(self.acct, self.pf, journal.read())      # restart = fold the journal
        self.now = None
        self.counters = Counters()
        self.last_rec = None
        self._dist = {}                                                   # entry intent id -> stop distance (cache)
        self._n_rec = 0
        self.hard_hold = None                                             # durability-unavailable HOLD (process)
        self.incidents = []                                               # (at_ms, text): surfaced, not journaled
        self._reads = {}                                                  # lost entry -> agreeing position reads
        self._refusals = {}                                               # stop intent -> venue refusal code
        if hard_hold is not None:                                         # boot directive: the store cannot write
            self.store_unavailable(hard_hold)

    # =============================================================================================== journal plumbing
    def _emit(self, cls, *, reason, **fields):
        if self.fold.last_sequence != self.journal.last_sequence():
            raise RuntimeError('journal moved under the runner (single-writer rule)')
        seq = self.journal.last_sequence() + 1
        ev = cls(event_id=ids.event_id(self.pf, seq), account_id=self.acct, aggregate_id=self.pf, sequence=seq,
                 at_ms=self.now, reason=reason, **fields)
        self.journal.append(ev)
        self.fold.apply(ev)
        return ev

    def _decision(self, *, decision_id, action, reason, authority, key, symbol, side, intents=(), subject_id=None,
                  detail='', evidence=()):
        if action in OPENING_ACTIONS:                                     # Codex ruling: opening risk is keyed
            if key is None:
                raise UnkeyedOpeningDecision(f'{action}: an opening decision needs a keys.decision_key key')
            check_decision_key(key)
            if key.purpose is not OPENING_ACTIONS[action] or decision_id != ids.derive_decision_id(self.acct, key):
                raise UnkeyedOpeningDecision(f'{action}: key purpose / decision id do not match the key')
        d = Decision(decision_id=decision_id, account_id=self.acct, at_ms=self.now, action=action, reason=reason,
                     authority=authority, key=key, evidence=tuple(evidence), symbol=symbol,
                     side=None if side is None else Side(side), subject_id=subject_id, detail=detail[:160],
                     intents=tuple(intents), policy_version=self.cfg.policy_version)
        self._emit(DecisionRecorded, reason=reason, decision=d)
        return d

    def _state(self, iv, to, reason=None):
        self._emit(IntentStateChanged, reason=reason or iv.intent.reason, intent_id=iv.intent_id, from_state=iv.state,
                   to_state=to)

    def _record_durable(self, planned):
        self._emit(IntentRecorded, reason=planned.reason, intent=dataclasses.replace(planned, state=IntentState.DURABLE))
        return self.fold.intents[planned.intent_id]

    def _ref(self, iv):
        cid = iv.intent.client_order_id
        return OrderRef(symbol=iv.intent.symbol, client_id=cid, route=route_of(iv.intent_id, cid) or 'classic')

    def _apply(self, iv, out, *, submit):
        """Record what a venue answer proves about one intent (and nothing when it proves nothing new)."""
        if not iv.live:
            return
        if iv.final is not None:                                          # crash after the durable FINAL result:
            self._state(iv, terminal_for(iv.final))                       # only its terminal step is missing
            return
        if submit and out.kind is OutcomeKind.REJECTED and out.error_code == DUPLICATE_CLIENT_ID:
            # a duplicate-id refusal proves the order EXISTS (an earlier send of this same intent landed): never
            # "refused, nothing executed"; read it by its client id and record what the venue holds
            out, submit = self.venue.query(self._ref(iv)), False
        res = result_from(out, iv.intent, ids.result_id(iv.intent_id, len(iv.results)), submit=submit)
        if res is None:
            return
        st = iv.state
        if res.phase is ResultPhase.FINAL:
            self._emit(ResultObserved, reason=iv.intent.reason, result=res)
            self._state(iv, terminal_for(res))
        elif res.phase is ResultPhase.KNOWN:
            if st in (IntentState.SUBMITTED, IntentState.UNKNOWN):
                self._emit(ResultObserved, reason=iv.intent.reason, result=res)
                self._state(iv, IntentState.WORKING)
        else:
            last = iv.results[-1] if iv.results else None
            if st is IntentState.UNKNOWN and last is not None and last.phase is ResultPhase.UNKNOWN and \
                    last.lookup == res.lookup:
                return                                                    # nothing new
            self._emit(ResultObserved, reason=iv.intent.reason, result=res)
            if st in (IntentState.SUBMITTED, IntentState.WORKING):
                self._state(iv, IntentState.UNKNOWN)

    def _resolve(self, iv):
        """One query for an intent whose answer was lost or acknowledged only."""
        if iv.live and iv.state in (IntentState.SUBMITTED, IntentState.UNKNOWN):
            self._apply(iv, self.venue.query(self._ref(iv)), submit=False)
            if self._not_found(iv) and iv.purpose in REDUCE_ONLY:
                self._resend(iv)                                          # never landed: same id, same route

    def _hold(self, reasons, *, reason=ReasonCode.RECONCILE_UNRECONCILED):
        reasons = tuple(dict.fromkeys((reason,) + tuple(reasons)))
        if self.fold.mode is EntriesMode.HOLD:
            return
        self.counters.holds += 1
        self._emit(ModeChanged, reason=reason, from_mode=self.fold.mode, to_mode=EntriesMode.HOLD, reasons=reasons,
                   from_hold=self.fold.hold, to_hold=HoldKind.NORMAL, decision_id=None, reconciliation_id=None)

    def _permits(self, purpose, op):
        return permitted(self.fold.mode, self.fold.hold, purpose, op) is Permission.ALLOWED

    # =============================================================================================== the cycle
    def cycle(self, now_ms, *, decide=True):
        if self.now is not None and now_ms < self.now:
            raise ValueError('the runner clock never goes back')
        self.now = now_ms
        self.counters.cycles += 1
        if self.hard_hold is not None:
            return self._hard_hold_cycle()
        try:
            return self._cycle(decide)
        except JournalUnavailable as ex:
            self._enter_hard_hold(ex)
            return self._hard_hold_cycle()

    def store_unavailable(self, ex):
        """The store reports it cannot write (a failed append, a boot verdict DURABILITY_UNAVAILABLE, a health probe):
        hard HOLD now (newcore.store.hold.durability_hold())."""
        if self.hard_hold is None:
            self._enter_hard_hold(ex)

    def _enter_hard_hold(self, ex):
        """The store cannot make anything durable (NC-02 A21). The HOLD (EntriesMode.HOLD + DURABILITY_UNAVAILABLE)
        cannot be journaled, so it lives in this process and is never left in-process (only a restart with a writable
        store and a reconciliation, A08). Only the A23 / A24 emergency set runs (_emergency_set). Surfaced through
        `hard_hold`, the incidents and the summary."""
        d = durability_hold()
        self.hard_hold = f'{d.reason}: {ex}'
        self.counters.hard_holds += 1
        self._incident(f'hard HOLD ({d.hold_kind}): {ex}')

    def _hard_hold_cycle(self):
        """The A24 emergency set from exchange truth, then a fresh reconciliation and the invariants (counted)."""
        self.counters.hard_hold_cycles += 1
        self._emergency_set()
        rec = self.reconcile()
        self.check_invariants(rec)
        return rec

    # ----------------------------------------------------------------------------------------------- A23 / A24
    def _owned_order(self, client_id):
        return client_id in self.fold.by_client_id or ids.is_emergency_client_id(client_id)

    def _emergency_set(self):
        """NC-02 A23 / A24, exactly the NC-01 permitted set for HOLD + DURABILITY_UNAVAILABLE (hard_hold_permits).
        Nothing here is journaled (nothing can be) or claimed as a result: every cycle and every restart re-derives it
        from exchange truth, and deterministic client ids make it idempotent.
          (1) narrow query: every owned risk-adding intent (ENTRY / ADD) by its client id;
          (3) drain: cancel it by client id while the venue shows it working; 'gone' is done; an unknown cancel is
              re-queried next cycle (re-cancelled only while it still shows working); never re-sent, repriced or
              fallen back;
          (4) adopt race / partial fills from exchange truth: the position itself is what (2) protects;
          (2) protection: each owned position's uncovered quantity gets ONE reduce-only stop with the emergency
              client id (exchange truth: account, symbol, side, quantity): found by that id -> confirmed, never
              duplicated. Existing protection is never cancelled (it is topped up, so nothing is removed before a
              replacement is confirmed); exposure never increases (reduce-only, quantity <= the position);
          (5) an incident per action (best-effort, outside the store).
        Forbidden and never done here: entries / adds, cancelling protection, repricing / fallback, management,
        market closes, resuming."""
        for iv in list(self.fold.live_intents()):
            if iv.purpose in OPENING_PURPOSES:
                self._drain(iv)
        if hard_hold_permits(Purpose.PROTECT, Op.PLACE) is not Permission.ALLOWED:
            return
        pos, oo = self.venue.positions(), self.venue.open_orders()
        if pos.kind is not ReadKind.OK or oo.kind is not ReadKind.OK:
            self._incident('hard HOLD: positions / open orders unreadable; the emergency set retries next cycle')
            return
        sides = {(x.symbol, x.side) for x in self.fold.open_lots()}
        sides |= {(iv.intent.symbol, str(iv.intent.side)) for iv in self.fold.intents.values()
                  if iv.purpose in OPENING_PURPOSES}
        sides |= {(o.ref.symbol, o.position_side) for o in oo.value if self._owned_order(o.ref.client_id)}
        for p in pos.value:
            if p.qty <= 0 or (p.symbol, p.side) not in sides:
                continue                                                  # flat, or foreign (A22: an item, untouched)
            covered = sum((o.qty for o in oo.value if o.reduce and o.order_type == 'STOP_MARKET'
                           and (o.ref.symbol, o.position_side) == (p.symbol, p.side)
                           and self._owned_order(o.ref.client_id)), ZERO)
            if covered >= p.qty:
                continue                                                  # M45: covered: no change
            self._emergency_stop(p, p.qty - covered)

    def _drain(self, iv):
        it = iv.intent
        if hard_hold_permits(it.purpose, Op.CANCEL) is not Permission.ALLOWED:
            return
        q = self.venue.query(self._ref(iv))
        if q.kind is not OutcomeKind.KNOWN:
            return            # FINAL: nothing rests (a fill is protected by (2)); NOT_FOUND / UNKNOWN: re-query later
        self.counters.drains += 1
        out = self.venue.cancel(self._ref(iv))
        if out.kind is OutcomeKind.REJECTED:                              # not open: a fill won the race (M51)
            out = self.venue.query(self._ref(iv))
        self._incident(f'hard HOLD drain {it.intent_id} ({it.client_order_id}): cancel -> {out.kind}'
                       f'{"" if out.executed_qty is None else f", executed {out.executed_qty}"}')

    def _emergency_stop(self, p, gap):
        rules = self.cfg.rules[p.symbol]
        qty = min(rules.quantize_qty(gap, Rounding.DOWN), p.qty)
        if qty <= 0:
            return
        price = self._emergency_stop_price(p)
        if price is None:
            self._incident(f'hard HOLD: no stop level for {p.symbol} {p.side} {qty}: unprotected, operator needed')
            return
        cid = ids.emergency_stop_client_id(self.acct, p.symbol, p.side, qty)
        ref = OrderRef(symbol=p.symbol, client_id=cid)
        found = self.venue.query(ref)                                     # (1) narrow query by the deterministic id
        if found.kind is OutcomeKind.KNOWN:
            return                                                        # already resting: confirmed, not duplicated
        out = self.venue.submit_stop(StopOrder(ref=ref, position_side=p.side, qty=qty, stop_price=price))
        if out.kind is OutcomeKind.REJECTED and out.error_code in ALGO_FALLBACK_CODES:    # same id, the algo route
            ref = OrderRef(symbol=p.symbol, client_id=cid, route='algo')
            out = self.venue.submit_stop(StopOrder(ref=ref, position_side=p.side, qty=qty, stop_price=price))
        if out.kind is OutcomeKind.REJECTED and out.error_code == DUPLICATE_CLIENT_ID:
            out = self.venue.query(ref)
        self.counters.emergency_stops += out.kind is OutcomeKind.KNOWN
        self._incident(f'hard HOLD emergency stop {cid} {p.symbol} {p.side} {qty} @ {price} -> {out.kind}')

    def _emergency_stop_price(self, p):
        """The owned stop level of that side: the lot's stop; for an adopted race fill with no lot, the entry's stop
        distance re-derived from the klines, from the venue's entry price (never tighter: floor / ceil to the tick)."""
        for lot in self.fold.open_lots():
            if (lot.symbol, lot.side) == (p.symbol, p.side):
                return self.stop_price_of(lot)
        rules = self.cfg.rules[p.symbol]
        for iv in reversed(list(self.fold.intents.values())):
            if iv.purpose in OPENING_PURPOSES and (iv.intent.symbol, str(iv.intent.side)) == (p.symbol, p.side):
                d = self._entry_distance(iv)
                if d is None:
                    return None
                if p.side == 'LONG':
                    return rules.quantize_price(p.entry_price - d, Rounding.DOWN)
                return rules.quantize_price(p.entry_price + d, Rounding.UP)
        return None

    def _cycle(self, decide):
        self._sync()
        rec = self.reconcile()
        if not rec.ok:
            self._hold([ITEM_REASON[k] for k, _ in rec.items])
        self._protect_all()
        self._handover_emergency()
        if decide:
            self._decide_all()
        rec = self.reconcile()
        if not rec.ok:
            self.counters.mismatch_cycles += 1
            self._hold([ITEM_REASON[k] for k, _ in rec.items])
        self.check_invariants(rec)
        return rec

    # ----------------------------------------------------------------------------------------------- 1. sync
    def _sync(self):
        for iv in list(self.fold.live_intents()):
            if iv.state in OPEN_STATES:
                out = self.venue.query(self._ref(iv))
                self._apply(iv, out, submit=False)
                if iv.state is IntentState.CANCELLING and out.kind is OutcomeKind.KNOWN:
                    self._apply(iv, self.venue.cancel(self._ref(iv)), submit=False)    # the cancel never reached it
                if self._not_found(iv):
                    if iv.purpose in REDUCE_ONLY:
                        self._resend(iv)
                    elif iv.purpose is Purpose.ENTRY:
                        self._corroborate_entry(iv)
            elif iv.state is IntentState.DURABLE:
                self._send_durable(iv)

    @staticmethod
    def _not_found(iv):
        return iv.live and iv.state is IntentState.UNKNOWN and iv.results and \
            iv.results[-1].lookup is Lookup.NOT_FOUND

    def _resend(self, iv):
        """Emergency set (also in HOLD): a reduce-only intent the venue does not know is sent again under the SAME
        deterministic client id. Idempotent: if an earlier send did land, the venue refuses the duplicate id and the
        refusal is read as "exists" (_apply). No new journal 'sent': one intent, one route, the same order."""
        it = iv.intent
        lot = next((x for x in self.fold.open_lots() if x.lot_id == it.owner_id), None)
        if lot is None:
            return                                                        # nothing left to protect / close
        self.counters.resends += 1
        if it.purpose is Purpose.PROTECT:
            out = self.venue.submit_stop(StopOrder(ref=self._ref(iv), position_side=str(it.side), qty=it.qty,
                                                   stop_price=it.stop_price))
            self._note_refusal(iv, out)
        else:
            out = self.venue.submit_market(MarketOrder(ref=self._ref(iv), position_side=str(it.side), qty=it.qty,
                                                       reduce=True))
        self._apply(iv, out, submit=True)

    def _corroborate_entry(self, iv):
        """An entry the venue does not know is NEVER assumed unfilled (NC-01). It is resolved only by >= 2 agreeing
        position reads taken after the visibility window (sent + NOT_FOUND_WINDOW_MS) and an explicit reconciliation
        decision: nothing beyond the owned lots -> NOT_FOUND_CORROBORATED (nothing executed); a surplus within the
        request -> POSITION_ADOPTED (the race / lost fill is owned and then protected). Anything else stays unknown."""
        it = iv.intent
        if iv.sent_at_ms is None or self.now < iv.sent_at_ms + NOT_FOUND_WINDOW_MS:
            return
        r = self.venue.positions(it.symbol)
        if r.kind is not ReadKind.OK:
            return
        pos = next((p for p in r.value if p.side == str(it.side)), None)
        qty = pos.qty if pos is not None else ZERO
        reads = self._reads.setdefault(iv.intent_id, [])
        if reads and reads[-1].qty != qty:
            reads.clear()                                                 # the position moved: start agreeing again
        if not reads or reads[-1].at_ms < self.now:
            reads.append(PositionRead(at_ms=self.now, qty=qty))
        if len(reads) < 2:
            return
        owned = sum((x.qty for x in self.fold.open_lots() if x.symbol == it.symbol and x.side == str(it.side)), ZERO)
        surplus = qty - owned
        if surplus < 0 or surplus > it.qty:
            self._incident(f'{iv.intent_id}: position {qty} vs owned {owned}: cannot attribute the lost entry')
            return
        did = ids.resolution_decision_id(iv.intent_id)
        self._decision(decision_id=did, action=Action.RECONCILE, reason=ReasonCode.RECONCILE_MATCH,
                       authority=Authority.RECONCILIATION, key=None, symbol=it.symbol, side=str(it.side),
                       subject_id=iv.intent_id, detail=f'lost entry: {len(reads)} reads, surplus {surplus}')
        adopted = surplus > 0
        res = OrderResult(result_id=ids.result_id(iv.intent_id, len(iv.results)), intent_id=iv.intent_id,
                          account_id=self.acct, client_order_id=it.client_order_id, phase=ResultPhase.FINAL,
                          requested_qty=it.qty, observed_at_ms=self.now, exchange_order_id=None, exchange_status=None,
                          lookup=None, executed_qty=surplus, avg_price=pos.entry_price if adopted else None,
                          evidence=Evidence.POSITION_ADOPTED if adopted else Evidence.NOT_FOUND_CORROBORATED,
                          corroboration=tuple(reads), resolved_by=did)
        self._emit(ResultObserved, reason=it.reason, result=res)
        self._state(iv, terminal_for(res))
        self._reads.pop(iv.intent_id, None)

    def _incident(self, what):
        self.incidents.append((self.now, what))
        self.counters.incidents += 1

    def _send_durable(self, iv):
        """A durable intent that was never sent (crash between intent_recorded and sent)."""
        p = iv.purpose
        if p is Purpose.ENTRY:
            key = self.fold.entry_key(iv)
            if self.now == key.candle_close_ms and self.fold.mode is EntriesMode.ACTIVE:
                self._send_entry(iv)
            else:
                self._emit(ResultObserved, reason=ReasonCode.LIFECYCLE_NOT_DURABLE,
                           result=not_sent_result(iv.intent, ids.result_id(iv.intent_id, len(iv.results)), self.now))
                self._state(iv, IntentState.NOT_SENT)
        elif p is Purpose.PROTECT:
            self._send_stop(iv)
        else:
            self._send_close(iv)

    # ----------------------------------------------------------------------------------------------- 2. reconcile
    def reconcile(self):
        """A fresh venue snapshot vs the fold. Returns the Reconciliation (items empty = match)."""
        self.counters.reconciliations += 1
        rid = ids.reconciliation_id(self.acct, self.now, self._n_rec)
        self._n_rec += 1
        pos, oo = self.venue.positions(), self.venue.open_orders()
        if pos.kind is not ReadKind.OK or oo.kind is not ReadKind.OK:
            rec = Reconciliation(self.now, rid, (('unreadable', 'positions / open orders'),), None, None)
            self.last_rec = rec
            return rec
        items = []
        expected, ambiguous = {}, set()
        for lot in self.fold.open_lots():
            k = (lot.symbol, lot.side)
            expected[k] = expected.get(k, ZERO) + lot.qty
        for iv in self.fold.live_intents():
            if iv.purpose in (Purpose.ENTRY, Purpose.CLOSE, Purpose.REDUCE) and iv.state in OPEN_STATES:
                ambiguous.add((iv.intent.symbol, str(iv.intent.side)))
        for p in pos.value:
            k = (p.symbol, p.side)
            if k in ambiguous:
                items.append(('ambiguous', f'{k[0]} {k[1]}: an order outcome is not known'))
            elif p.qty != expected.get(k, ZERO):
                items.append(('position', f'{k[0]} {k[1]}: venue {p.qty}, owned {expected.get(k, ZERO)}'))
        listed = {}
        for o in oo.value:
            listed[o.ref.client_id] = o
            iv = self.fold.by_client_id.get(o.ref.client_id)
            if iv is None and ids.is_emergency_client_id(o.ref.client_id):
                items.append(('emergency_stop', o.ref.client_id))     # A23 stop: adopted only by reconciliation (A08)
            elif iv is None:
                items.append(('foreign_order', o.ref.client_id))
            elif not iv.live:
                items.append(('orphan_order', o.ref.client_id))
            elif (o.qty, o.stop_price) != (iv.intent.qty, iv.intent.stop_price):
                items.append(('order_mismatch', o.ref.client_id))
        for iv in self.fold.live_intents():
            if iv.purpose is Purpose.PROTECT and iv.state is IntentState.WORKING and \
                    iv.intent.client_order_id not in listed:
                items.append(('stop_missing', iv.intent.client_order_id))
        rec = Reconciliation(self.now, rid, tuple(items), pos.value, oo.value)
        self.last_rec = rec
        return rec

    # ----------------------------------------------------------------------------------------------- 3. protect
    def _protect_all(self):
        for lot in self.fold.open_lots():
            d = self.fold.pending_closes.get(lot.lot_id)
            if d is not None and lot.closing is None:                     # a decided close a crash interrupted:
                self._close_lot(lot, reason=d.reason, key=d.key)          # finish it (never a fresh stop first)
            self._secure(lot.lot_id)

    def _lot(self, lot_id):
        return next((x for x in self.fold.open_lots() if x.lot_id == lot_id), None)

    def _secure(self, lot_id):
        """Make an open lot protected or closing, with BOUNDED attempts (never recursion): up to SECURE_ROUNDS x
        (one stop attempt; if the venue refuses it, one reduce-only market close). Still neither -> durable HOLD and an
        incident; the next cycle tries again under the same bound."""
        for _ in range(SECURE_ROUNDS):
            lot = self._lot(lot_id)
            if lot is None or lot.live_stop is not None or lot.closing is not None:
                return
            n = len(lot.protects)
            self._protect(lot)
            lot = self._lot(lot_id)
            if lot is None or lot.live_stop is not None or lot.closing is not None:
                return
            if len(lot.protects) == n or lot.protects[-1].state is not IntentState.REJECTED:
                return                                                    # nothing was attempted (not permitted)
            if self._next_route(lot) == 'algo':
                continue                                                  # the refusal names the algo route
            self._close_lot(lot, reason=ReasonCode.EXIT_STOP_FAILED, key=None)    # the stop was refused
        lot = self._lot(lot_id)
        if lot is not None and lot.live_stop is None and lot.closing is None:
            self._incident(f'{lot_id}: stop and close refused {SECURE_ROUNDS} times this cycle')
            self._hold([ReasonCode.EXEC_STOP_FAILED], reason=ReasonCode.EXEC_STOP_FAILED)

    def _entry_distance(self, entry_iv):
        """The stop distance of an entry: cached, or re-derived from the klines at its key candle (exchange truth)."""
        d = self._dist.get(entry_iv.intent_id)
        if d is not None:
            return d
        key = self.fold.entry_key(entry_iv)
        sym, side = entry_iv.intent.symbol, str(entry_iv.intent.side)
        r = self.bars.closed_bars(sym, self.cfg.tf_ms, as_of_ms=key.candle_close_ms, limit=self.signals.window)
        if r.kind is ReadKind.OK:
            for s in self.signals.decide(sym, r.value, key.candle_close_ms):
                if s.action == ENTER and s.side == side and s.candle_close_ms == key.candle_close_ms:
                    self._dist[entry_iv.intent_id] = s.stop_distance
                    return s.stop_distance
        return None

    def _stop_distance(self, lot):
        d = self._entry_distance(lot.entry)
        if d is None:
            raise LookupError(f'{lot.lot_id}: the entry signal cannot be re-derived from the bars')
        return d

    def stop_price_of(self, lot):
        if lot.protects:
            return lot.protects[0].intent.stop_price
        rules = self.cfg.rules[lot.symbol]
        d = self._stop_distance(lot)
        if lot.side == 'LONG':
            return rules.quantize_price(lot.avg_price - d, Rounding.DOWN)       # never tighter than the R
        return rules.quantize_price(lot.avg_price + d, Rounding.UP)

    def _handover_emergency(self):
        """Store writable again (after a restart): an A23 emergency stop left on the venue is retired only AFTER the
        journaled protection of that side is confirmed WORKING and covers the position (place, confirm, then cancel
        the old one), or when that side is flat (an orphan reduce-only order: cancel-only work). It was never
        journaled, so its cancel is exchange cleanup, reported as an incident; reconciliation then sees no item."""
        rec = self.last_rec
        if rec is None or rec.orders is None or not any(ids.is_emergency_client_id(o.ref.client_id)
                                                         for o in rec.orders):
            return
        pos, oo = self.venue.positions(), self.venue.open_orders()
        if pos.kind is not ReadKind.OK or oo.kind is not ReadKind.OK:
            return
        qty = {(p.symbol, p.side): p.qty for p in pos.value}
        for o in oo.value:
            if not (o.reduce and ids.is_emergency_client_id(o.ref.client_id)):
                continue
            k = (o.ref.symbol, o.position_side)
            journaled = sum((x.live_stop.intent.qty for x in self.fold.open_lots()
                             if (x.symbol, x.side) == k and x.live_stop is not None
                             and x.live_stop.state is IntentState.WORKING), ZERO)
            if qty.get(k, ZERO) == 0 or journaled >= qty[k]:
                out = self.venue.cancel(o.ref)
                self._incident(f'emergency stop {o.ref.client_id} retired after handover -> {out.kind}')

    def _next_route(self, lot):
        """Step 0 r2 route attempts: 'algo' only right after a CLASSIC protect attempt of this lot that closed
        REJECTED with a code that names the algo service (ALGO_FALLBACK_CODES), or with an unknown code (a restart
        between the routes lost it: protection outranks, so the algo route is tried once). Never after an UNKNOWN
        attempt (that one is resolved by query / same-id re-send first) and never twice (G6)."""
        if not lot.protects:
            return 'classic'
        prev = lot.protects[-1]
        if prev.state is not IntentState.REJECTED or route_of(prev.intent_id, prev.intent.client_order_id) != 'classic':
            return 'classic'
        code = self._refusals.get(prev.intent_id, 'unknown')
        return 'algo' if code == 'unknown' or code in ALGO_FALLBACK_CODES else 'classic'

    def _protect(self, lot):
        if not lot.open or lot.live_stop is not None or lot.closing is not None:
            return
        if not self._permits(Purpose.PROTECT, Op.PLACE):
            return
        n = len(lot.protects)
        iid = self.journal.gate().grammar.next_child_intent_id(lot.lot_id, Purpose.PROTECT)    # read just before
        did = ids.child_decision_id(iid)
        prior = self.journal.find_decision(did)
        if prior is not None:                                             # crash gap: decided, intent not recorded
            planned = prior.decision.intents[0]
        else:
            price = self.stop_price_of(lot)
            route = self._next_route(lot)
            reason = ReasonCode.PROTECT_PLACE if n == 0 or route == 'algo' else ReasonCode.PROTECT_RESTORING
            planned = planned_intent(intent_id=iid, account_id=self.acct, decision_id=did, purpose='protect',
                                     symbol=lot.symbol, side=lot.side, qty=lot.qty, reason=reason, at_ms=self.now,
                                     owner_id=lot.lot_id, stop_price=price, route=route)
            self._decision(decision_id=did, action=Action.PROTECT, reason=reason, authority=Authority.PROTECTION,
                           key=None, symbol=lot.symbol, side=lot.side, intents=(planned,), subject_id=lot.lot_id,
                           evidence=(lot.entry.intent_id,), detail=f'stop {price} x {lot.qty}')
        self._send_stop(self._record_durable(planned))

    def _send_stop(self, iv):
        self._state(iv, IntentState.SUBMITTED)
        it = iv.intent
        out = self.venue.submit_stop(StopOrder(ref=self._ref(iv), position_side=str(it.side), qty=it.qty,
                                               stop_price=it.stop_price))
        self._note_refusal(iv, out)
        self._apply(iv, out, submit=True)
        self._resolve(iv)                                                 # a refused stop is handled by _secure

    def _note_refusal(self, iv, out):
        """The venue's refusal code of a stop attempt (in memory: NC-01 results carry no error code)."""
        if out.kind is OutcomeKind.REJECTED and out.error_code != DUPLICATE_CLIENT_ID:
            self._refusals[iv.intent_id] = out.error_code

    # ----------------------------------------------------------------------------------------------- 4. decide
    def _decide_all(self):
        """Decide phase (overridden by the multi-symbol book: all closes first, then entries in priority order)."""
        for sym in self.cfg.symbols:
            self._decide(sym)

    def _bars_now(self, symbol):
        r = self.bars.closed_bars(symbol, self.cfg.tf_ms, as_of_ms=self.now, limit=self.signals.window)
        if r.kind is not ReadKind.OK or not r.value or r.value[-1].close_ms != self.now:
            return None                                                   # no fresh closed candle: no decision
        return r.value

    def _sizing_equity(self):
        """The equity entries are sized on (a ReadOutcome); the book snapshots it once per cycle."""
        return self.reads.equity()

    def _open_notional(self):
        return sum((x.qty * x.avg_price for x in self.fold.open_lots()), ZERO)

    def _decide(self, symbol):
        bars = self._bars_now(symbol)
        if bars is None:
            return
        for s in self.signals.decide(symbol, bars, self.now):
            if s.side not in self.cfg.sides:
                continue
            if s.action == CLOSE:
                self._exit(symbol, s)
            elif s.action == ENTER:
                self._enter(symbol, s, bars[-1].close)

    def _key(self, symbol, s, purpose):
        """The one runner-boundary key constructor (step 0 r2): strategy = '<name>@<tf>', version v<n>."""
        return ids.decision_key(self.signals.name, self.signals.version, self.signals.tf_label, symbol, s.side,
                                s.candle_close_ms, purpose)

    def _enter(self, symbol, s, ref_price):
        key = self._key(symbol, s, Purpose.ENTRY)
        did = ids.derive_decision_id(self.acct, key)
        prior = self.journal.find_decision(did)
        if prior is not None:                                             # consumed: never decided again
            self.counters.redelivered += 1
            d = prior.decision
            if d.action is Action.ENTER and d.intents and d.intents[0].intent_id not in self.fold.intents and \
                    self.now == key.candle_close_ms and self.fold.mode is EntriesMode.ACTIVE:
                self._dist[d.intents[0].intent_id] = s.stop_distance      # crash gap: derived intent, still in window
                self._send_entry(self._record_durable(d.intents[0]))
            return
        gate = self._entry_gate(symbol, s.side)
        sz = None
        if gate is None:
            eq = self._sizing_equity()
            if eq.kind is not ReadKind.OK:
                gate = ReasonCode.CONNECTIVITY_EXCHANGE_OUTAGE
            else:
                notional = self._open_notional()
                sz = size_entry(equity=eq.value[0], stop_distance=s.stop_distance, ref_price=ref_price,
                                rules=self.cfg.rules[symbol], policy=self.cfg.sizing, open_notional=notional)
                gate = sz.reason
        if gate is not None:
            self.counters.skips += 1
            self._decision(decision_id=did, action=Action.SKIP, reason=gate, authority=Authority.STRATEGY, key=key,
                           symbol=symbol, side=s.side, detail=f'skip {gate}')
            return
        iid = ids.derive_intent_id(self.acct, key)
        planned = planned_intent(intent_id=iid, account_id=self.acct, decision_id=did, purpose='entry', symbol=symbol,
                                 side=s.side, qty=sz.qty, reason=s.reason, at_ms=self.now, slot_id=self.cfg.slot_id)
        self._decision(decision_id=did, action=Action.ENTER, reason=s.reason, authority=Authority.STRATEGY, key=key,
                       symbol=symbol, side=s.side, intents=(planned,),
                       detail=f'risk {sz.risk_usd:.8f} dist {s.stop_distance} capped {sz.capped}')
        self._dist[iid] = s.stop_distance
        self.counters.entries += 1
        self._send_entry(self._record_durable(planned))

    def _entry_gate(self, symbol, side):
        if self.fold.mode is not EntriesMode.ACTIVE:
            return GATE_REASON[self.fold.mode]
        if any(x.symbol == symbol and x.side == side for x in self.fold.open_lots()):
            return ReasonCode.CAPACITY_IN_TRADE
        if self.fold.live_entries(symbol, side):
            return ReasonCode.CAPACITY_ENTRY_WORKING
        return None

    def _send_entry(self, iv):
        self._state(iv, IntentState.SUBMITTED)
        it = iv.intent
        out = self.venue.submit_market(MarketOrder(ref=self._ref(iv), position_side=str(it.side), qty=it.qty,
                                                   reduce=False))
        self._apply(iv, out, submit=True)
        if iv.live:                                                       # lost / acknowledged answer: HOLD, resolve
            self._hold([ReasonCode.EXEC_ENTRY_UNCONFIRMED], reason=ReasonCode.EXEC_ENTRY_UNCONFIRMED)
            self._resolve(iv)
        if iv.final is not None and iv.executed > 0:
            self._secure(ids.derive_lot_id(self.acct, iv.intent_id))

    def _exit(self, symbol, s):
        lot = next((x for x in self.fold.open_lots() if x.symbol == symbol and x.side == s.side), None)
        if lot is None or lot.closing is not None:
            return                                                        # nothing to close: not a decision
        key = self._key(symbol, s, Purpose.CLOSE)
        if self.journal.find_decision(ids.derive_decision_id(self.acct, key)) is not None:
            self.counters.redelivered += 1
            if ids.derive_intent_id(self.acct, key) in self.fold.intents:
                return                                                    # already recorded: sync owns it
        self._close_lot(lot, reason=s.reason, key=key)                    # (re)entered: a crash gap resumes here
        self._secure(lot.lot_id)                                          # the close failed: re-protect at once

    def _close_lot(self, lot, *, reason, key):
        """Cancel the stop, then a reduce-only market close of the whole lot. key=None: an unkeyed (protection) close."""
        if key is not None:
            did = ids.derive_decision_id(self.acct, key)
            iid = ids.derive_intent_id(self.acct, key)
            authority = Authority.STRATEGY
        else:
            iid = self.journal.gate().grammar.next_child_intent_id(lot.lot_id, Purpose.CLOSE)
            did = ids.child_decision_id(iid)
            authority = Authority.PROTECTION
        prior = self.journal.find_decision(did)
        if prior is not None:                                             # crash gap: decided, intent not recorded
            planned = prior.decision.intents[0]
        else:
            planned = planned_intent(intent_id=iid, account_id=self.acct, decision_id=did, purpose='close',
                                     symbol=lot.symbol, side=lot.side, qty=lot.qty, reason=reason, at_ms=self.now,
                                     owner_id=lot.lot_id)
            self._decision(decision_id=did, action=Action.CLOSE, reason=reason, authority=authority, key=key,
                           symbol=lot.symbol, side=lot.side, intents=(planned,), subject_id=lot.lot_id,
                           detail=f'close {lot.qty}')
        if planned.intent_id in self.fold.intents:
            return
        stop = lot.live_stop
        if stop is not None:
            if stop.state is not IntentState.CANCELLING:
                self._state(stop, IntentState.CANCELLING, reason)
            out = self.venue.cancel(self._ref(stop))
            self._apply(stop, out, submit=False)
            if stop.live:
                self._apply(stop, self.venue.query(self._ref(stop)), submit=False)
            if stop.live:                                                 # stop state unknown: do not close blind
                self._hold([ReasonCode.PROTECT_CHECKING])
                return
        lot = next((x for x in self.fold.open_lots() if x.lot_id == lot.lot_id), None)
        if lot is None:
            return                                                        # the stop filled first: nothing to close
        self._send_close(self._record_durable(planned))

    def _send_close(self, iv):
        self._state(iv, IntentState.SUBMITTED)
        it = iv.intent
        out = self.venue.submit_market(MarketOrder(ref=self._ref(iv), position_side=str(it.side), qty=it.qty,
                                                   reduce=True))
        self._apply(iv, out, submit=True)
        if iv.live:
            self._hold([ReasonCode.EXEC_ORDER_FAILED], reason=ReasonCode.EXEC_ORDER_FAILED)
            self._resolve(iv)                                             # a refused close is handled by _secure

    # ----------------------------------------------------------------------------------------------- operator
    def resume(self, now_ms):
        """Leave HOLD: needs a clean fresh reconciliation and records an operator RESUME decision."""
        self.now = max(self.now or now_ms, now_ms)
        if self.fold.mode is EntriesMode.ACTIVE:
            return True
        rec = self.reconcile()
        if not rec.ok:
            return False
        did = ids.operator_decision_id(self.acct, 'resume', self.now)
        self._decision(decision_id=did, action=Action.RESUME, reason=ReasonCode.OPERATOR_RESUME,
                       authority=Authority.OPERATOR, key=None, symbol=None, side=None, evidence=(), detail='resume')
        self._emit(ModeChanged, reason=ReasonCode.OPERATOR_RESUME, from_mode=self.fold.mode, to_mode=EntriesMode.ACTIVE,
                   reasons=(), from_hold=self.fold.hold, to_hold=None, decision_id=did,
                   reconciliation_id=rec.reconciliation_id)
        return True

    # ----------------------------------------------------------------------------------------------- 5. invariants
    def check_invariants(self, rec):
        problems = []
        pf = None
        try:
            pf = self.portfolio(rec)
        except Exception as ex:                                           # NC-01 invariant refused the state
            problems.append(f'I3 {type(ex).__name__}: {ex}')
        if rec.positions is not None and rec.orders is not None:
            # I1: NC-01 confirmed_coverage (only a WORKING carrier counts; an unpromoted replacement never does) of
            # the lots of each (symbol, side), and every counted carrier must be listed on the venue right now
            listed = {o.ref.client_id for o in rec.orders if o.reduce}
            cover = {}
            if pf is not None and pf.ownership is Ownership.KNOWN:
                live = pf.intents_by_id()
                for lot in pf.lots:
                    c = confirmed_coverage(lot.stop, live)
                    if c and live[lot.stop.order].client_order_id in listed:
                        cover[(lot.symbol, str(lot.side))] = cover.get((lot.symbol, str(lot.side)), ZERO) + c
            for o in rec.orders:                                  # an A23 emergency stop is owned protection too
                if o.reduce and ids.is_emergency_client_id(o.ref.client_id):
                    cover[(o.ref.symbol, o.position_side)] = cover.get((o.ref.symbol, o.position_side), ZERO) + o.qty
            for p in rec.positions:
                if p.qty != 0 and cover.get((p.symbol, p.side), ZERO) < p.qty:       # under-covered = naked
                    problems.append(f'I1 {p.symbol} {p.side}: position {p.qty}, confirmed stop '
                                    f'{cover.get((p.symbol, p.side), ZERO)}')
        cids = self.fold.client_ids_recorded
        if len(cids) != len(set(cids)):
            problems.append('I2 a client id was recorded twice')
        keys = [self.fold.entry_key(iv) for iv in self.fold.intents.values() if iv.purpose is Purpose.ENTRY]
        if len(keys) != len(set(keys)):
            problems.append('I2 two entry intents for one DecisionKey')
        if problems:
            naked = any(p.startswith('I1') for p in problems)
            self.counters.unprotected_cycles += naked
            if self.fold.mode is not EntriesMode.HOLD and self.hard_hold is None:
                if naked:                                  # never ACTIVE while naked: stop new risk at once (durable)
                    self._incident('; '.join(problems))
                    self._hold([ReasonCode.PROTECT_CHECKING], reason=ReasonCode.PROTECT_CHECKING)
                if self.cfg.strict:
                    raise InvariantBreach('; '.join(problems))
        return problems

    # ----------------------------------------------------------------------------------------------- projections
    def _fee(self, symbol, eoid):
        r = self.venue.fills(symbol, eoid)
        return sum((f.fee for f in r.value), ZERO) if r.kind is ReadKind.OK else ZERO

    def portfolio(self, rec=None):
        """The NC-01 Portfolio this fold + reconciliation proves (constructing it runs every NC-01 invariant)."""
        rec = rec or self.last_rec
        f = self.fold
        mode, hold = f.mode, f.hold
        reasons = f.mode_reasons if mode is not EntriesMode.ACTIVE else ()
        since = f.mode_since_ms if f.mode_since_ms is not None else (f.first_at_ms or self.now)
        common = dict(portfolio_id=self.pf, account_id=self.acct, generation=f.last_sequence, entries_mode=mode,
                      mode_since_ms=since, pause_reasons=reasons, hold_kind=hold)
        lots = f.open_lots()
        live = f.live_intents()
        if not lots and not live:
            if rec is not None and rec.ok and rec.flat:
                proof = OwnershipProof(kind=ProofKind.FLAT_SNAPSHOT, at_ms=rec.at_ms,
                                       reconciliation_id=rec.reconciliation_id,
                                       key_digest=self.cfg.account.binding.key_digest, decision_id=None,
                                       through_sequence=None)
                pf = Portfolio(ownership=Ownership.KNOWN_EMPTY, proof=proof, positions=(), intents=(), entry_stops=(),
                               **common)
                check_flat_snapshot_fresh(self.cfg.account, pf, self.now, 0)
                return pf
            if mode is EntriesMode.HOLD:
                return Portfolio(ownership=Ownership.UNKNOWN, proof=None, positions=None, intents=None,
                                 entry_stops=None, **common)
            raise ValueError('nothing owned but no fresh flat snapshot: KNOWN_EMPTY is not proven')
        listed = {o.ref.client_id for o in (rec.orders or ())} if rec is not None else set()
        by_pos = {}
        for lot in lots:
            stop_iv = lot.live_stop
            price = stop_iv.intent.stop_price if stop_iv is not None else self.stop_price_of(lot)
            qty = stop_iv.intent.qty if stop_iv is not None else lot.qty
            confirmed = rec.at_ms if (stop_iv is not None and stop_iv.state is IntentState.WORKING and
                                      stop_iv.intent.client_order_id in listed) else None
            prot = Protection(owner_id=lot.lot_id, price=price, qty=qty,
                              order=None if stop_iv is None else stop_iv.intent_id, replacement=None,
                              confirmed_at_ms=confirmed, miss=None)
            e = lot.entry.final
            fills = [Fill(at_ms=lot.opened_at_ms, reason=lot.entry.intent.reason, qty=lot.initial_qty,
                          price=lot.avg_price, fee=self._fee(lot.symbol, e.exchange_order_id), result_id=e.result_id,
                          decision_id=None)]
            for c in lot.closings:
                fills.append(Fill(at_ms=c.at_ms, reason=c.reason, qty=c.qty, price=c.price,
                                  fee=self._fee(lot.symbol, c.exchange_order_id), result_id=c.result_id,
                                  decision_id=None))
            dist = abs(lot.avg_price - self.stop_price_of(lot))
            closing = lot.closing
            rec_lot = Lot(lot_id=lot.lot_id, account_id=self.acct, symbol=lot.symbol, side=Side(lot.side),
                          source=LotSource.STRATEGY, slot_id=self.cfg.slot_id, timeframe=self.cfg.timeframe,
                          opened_at_ms=lot.opened_at_ms, qty=lot.qty, avg_price=lot.avg_price,
                          initial_qty=lot.initial_qty, max_qty=lot.initial_qty, risk_distance=dist,
                          risk_usd=lot.initial_qty * dist, stop=prot, fills=tuple(fills),
                          in_flight=None if closing is None else closing.intent_id, adopted_by=None, tp1_done=False,
                          ladder_done=(), adds_done=0)
            by_pos.setdefault((lot.symbol, lot.side), []).append(rec_lot)
        positions = tuple(Position(position_id=ids.position_id(self.acct, s, sd), symbol=s, side=Side(sd),
                                   lots=tuple(v)) for (s, sd), v in sorted(by_pos.items()))
        proof = OwnershipProof(kind=ProofKind.JOURNAL, at_ms=self.now, reconciliation_id=None, key_digest=None,
                               decision_id=None, through_sequence=f.last_sequence)
        pf = Portfolio(ownership=Ownership.KNOWN, proof=proof, positions=positions,
                       intents=tuple(iv.current() for iv in live), entry_stops=(), **common)
        check_account_portfolio(self.cfg.account, pf)
        return pf

    def trades(self):
        """TradeOutcome of every closed lot, in entry order."""
        return [trade_outcome(self.fold, lot, self.venue, self.reads, self.cfg.tf_ms, self.stop_price_of(lot))
                for lot in self.fold.lots() if not lot.open]

    def summary(self):
        eq = self.reads.equity()
        try:
            own = str(self.portfolio().ownership)
        except Exception:
            own = None
        return summarize(self.trades(), equity_end=eq.value[0] if eq.kind is ReadKind.OK else None,
                         open_lots=len(self.fold.open_lots()), ownership=own,
                         mode=str(self.fold.mode) if self.hard_hold is None else 'hold(durability_unavailable)',
                         counters=dataclasses.asdict(self.counters))
