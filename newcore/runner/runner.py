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
  4. decide      strategy signals on the closed candles: CLOSE first (a reduce-only market close WITH the stop still
                 live; the stop is cancelled only once the close has made the lot flat - Codex ruling 13 / TNET N6), then
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
import re
import time
from dataclasses import dataclass, field
from decimal import Context, Decimal

from newcore.domain import (Account, Action, Authority, Decision, DecisionRecorded, EntriesMode, Environment, HoldKind,
                            InstrumentRules, IntentRecorded, IntentState, IntentStateChanged, Lot, LotSource,
                            ModeChanged, Op, Ownership, OwnershipProof, Portfolio, Position, ProofKind, Protection,
                            Purpose, ReasonCode, ResultObserved, ResultPhase, Rounding, Side, check_account_portfolio,
                            check_flat_snapshot_fresh, confirmed_coverage, permitted)
from newcore.domain.modes import Permission
from newcore.domain import Evidence, Lookup, OrderResult
from newcore.domain.orders import NOT_FOUND_WINDOW_MS, REDUCE_ONLY, PositionRead, terminal_for
from newcore.domain.portfolio import Fill
from newcore.ports.journal import JournalUnavailable
from newcore.domain import Incident, IncidentRecorded
from newcore.domain.errors import DomainError
from newcore.store.hold import durability_hold, hard_hold_permits
from newcore.ports.keys import check_decision_key, route_of
from newcore.ports.venue import MarketOrder, OrderOutcome, OrderRef, OutcomeKind, ReadKind, ReadOutcome, StopOrder

from . import ids
from .fold import Fold, OPEN_STATES
from .outcome import summarize, trade_outcome
from .records import not_sent_result, planned_intent, result_from
from .fill_evidence import EvidencePending, rows_of
from .redact import describe, exc_tag
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
EMERGENCY_GENERATIONS = 16            # hard HOLD: cover generations per (symbol, side, gap size)
ESCALATE_AFTER = 2                    # cycles with an unconfirmed stop before the reduce-only close (Cowork NEW-4)
LOST_ENTRY_LOOKBACK = 3               # the lost-entry search after the boot search (mid-run mismatches)
GUARD_ENTRY_LOOKBACK = 60             # the guard (no journal): candles back an own entry is proven by its client id
BAR_PAGE = 1500                       # the BarSource port's per-read limit (Binance klines max)
MAX_LOST_TAIL_CANDLES = 2000          # runtime lost-tail search: hard bound (4h: ~333 days)
GUARD_CHILDREN = 32                   # the guard: lineage ordinals per purpose probed for our own exits / adds
GUARD_CHILD_MISSES = 4                # ... until this many consecutive unknown ordinals (unsent ones leave gaps)
REDUCE_REFUSALS = 2                   # refused reduce-only sends of a lot before the venue position is read (MED-3)
EXTERNAL_CLOSE_CODES = (-2022, -4061)  # reduce only rejected / position side does not match: nothing to reduce
E_WOULD_TRIGGER = -2021               # Binance "Order would immediately trigger"
# Cowork F3, Codex ruling (accepted as an EMERGENCY route only): a hard-HOLD emergency stop refused as 'would trigger'
# is re-placed EMERGENCY_FALLBACK_BUFFER beyond the protective side of the CURRENT MARK (and of the last close), on the
# tick, inside the price filter; it is DEGRADED protection (incident + health line). When no safe stop can be confirmed
# (no mark, no valid level, refused again) the exposure escalates to a deterministic reduce-only market close.
F3_POLICY = 'fallback_stop'
EMERGENCY_FALLBACK_BUFFER = Decimal('0.005')   # a bounded emergency constant (never a strategy parameter)
OPENING_PURPOSES = frozenset({Purpose.ENTRY, Purpose.ADD})


ALGO_ROUTE = 'algo_route'            # TestnetVenue detail: the refusal names the algo service (-4120 and friends)
ALGO_TRIGGERED = 'algo_triggered'    # TestnetVenue detail: an algo stop triggered; exchange_order_id = the child order
DUPLICATE_DETAIL = 'duplicate_client_id'   # TestnetVenue: a duplicate client id comes back UNKNOWN with this detail
OPEN_EXCHANGE_STATUSES = ('NEW', 'PARTIALLY_FILLED')
RCTX = Context(prec=34)


DIST_TOKEN = re.compile(r'(?:^| )dist ([0-9]+(?:\.[0-9]+)?(?:E[+-]?[0-9]+)?)(?: |$)')


def journaled_distance(decision):
    """H1: the planned stop distance an ENTER decision recorded ('dist <Decimal>' in its detail, exact text), or None."""
    if decision is None or decision.action is not Action.ENTER:
        return None
    m = DIST_TOKEN.search(decision.detail)
    if m is None:
        return None
    d = Decimal(m.group(1))
    return d if d > 0 else None


def _is_duplicate(out):
    """The order EXISTS: the raw -4116 refusal, or TestnetVenue's UNKNOWN 'duplicate_client_id' form."""
    return (out.kind is OutcomeKind.REJECTED and out.error_code == DUPLICATE_CLIENT_ID) or \
        (out.kind is OutcomeKind.UNKNOWN and out.detail == DUPLICATE_DETAIL)


def _suggests_algo(out):
    """A refusal that names the algo route: TestnetVenue detail 'algo_route', or one of the transport's codes."""
    return out.kind is OutcomeKind.REJECTED and (out.detail == ALGO_ROUTE or out.error_code in ALGO_FALLBACK_CODES)


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
    raw_qty: Decimal | None = None             # H2 (TNET-01 T10b only): send THIS entry quantity unsized and unfiltered,
                                               # so the venue's own min-qty / min-notional refusal is exercised;
                                               # refused unless the account is bound to TESTNET


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


_ORDER_CALLS = frozenset({'submit_market', 'submit_stop', 'cancel', 'query'})
_READ_CALLS = frozenset({'positions', 'open_orders', 'fills', 'trades', 'mark_price'})


class _SafeVenue:
    """The Runner's view of the VenuePort (Cowork adv6, EXC class): a venue call that RAISES instead of answering breaks
    the typed-outcome contract; it is taken as an UNKNOWN answer (order calls) / an UNKNOWN read, with an incident, so
    the cycle goes on (every other lot is still protected) and the unknown-stop escalation and the durable HOLD run as
    for a lost answer. BaseExceptions (KeyboardInterrupt, SystemExit, a test's simulated process death) still stop it.
    Attributes the adapter does not have stay absent (duck-typed reads such as trades / mark_price)."""

    def __init__(self, inner, runner):
        self._inner, self._runner = inner, runner

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr) or name not in _ORDER_CALLS | _READ_CALLS:
            return attr

        def call(*args, **kwargs):
            try:
                return attr(*args, **kwargs)
            except Exception as ex:                                       # noqa: BLE001 - the contract break itself
                return self._runner._venue_raised(name, args, ex)
        return call


class _Side:
    """(symbol, side) with the attribute names the provenance helpers read."""
    __slots__ = ('symbol', 'side')

    def __init__(self, symbol, side):
        self.symbol, self.side = symbol, side


class Runner:
    @property
    def venue(self):
        return self._venue

    @venue.setter
    def venue(self, v):                                                   # adv6: always behind the raising guard
        self._venue = v if isinstance(v, _SafeVenue) else _SafeVenue(v, self)

    @property
    def mode(self):
        """The EFFECTIVE entries mode (Cowork adv6 E / h7) - THE authoritative mode for every caller: HOLD whenever a
        hard HOLD is active. fold.mode is only the journal's durable view (it cannot record a HOLD while the store is
        down); portfolio().entries_mode / hold_kind, the health line and the summary all derive from this."""
        return EntriesMode.HOLD if self.hard_hold is not None else self.fold.mode

    @property
    def hold(self):
        """The effective HOLD kind: DURABILITY_UNAVAILABLE in a hard HOLD, else the journal's."""
        return HoldKind.DURABILITY_UNAVAILABLE if self.hard_hold is not None else self.fold.hold

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
        self._ext_partial = {}                                            # lot -> venue qty after an external partial
        self._em_filled, self._em_closed_ids, self._em_left = {}, set(), {}   # hard-HOLD emergency fills (in process)
        self._em_sent = {}                                                # (symbol, side) -> {(zbn1e cid, route)} sent
        self._own_unknown = set()                                         # lots whose ownership evidence is unreadable
        self._pending_trades = set()                                      # closed lots not bookable yet (h6)
        self._pending_evidence = set()                                    # projection evidence pending (incident once)
        self._boot_hard_hold = hard_hold is not None                      # a previous process may have sent zbn1e
        self._first_now = None                                            # this process's first cycle (candle close)
        self._reads = {}                                                  # lost entry -> agreeing position reads
        self._refusals = {}                                               # stop intent -> venue refusal code
        self._orphans_checked = set()                                     # ENTER decisions asked about (289)
        self._unconfirmed = {}                                            # lot -> (cycle, cycles without a stop)
        self._reduce_refused = {}                                         # lot -> consecutive 'nothing to reduce'
        self._suspended_lots = set()                                      # lots with an external-close owner item
        self._lost_checked = set()                                        # (symbol, side, candle) asked (289)
        last = journal.last_sequence()                                    # the journal's last DURABLE state at boot
        tail = tuple(journal.read(after_sequence=last - 1)) if last else ()
        self._boot_last_at = tail[-1].at_ms if tail else None             # anchors the lost-tail search (NEW A)
        self._deep_search_done = False                                    # ... until one boot search completed
        self.guard = False                                                # app guard: no trusted journal at all
        self.degraded = {}                                                # (symbol, side) -> degraded protection
        self._listed = None                                               # this sync's listed client ids (LOW-6)
        if config.raw_qty is not None and (config.account.binding.environment is not Environment.TESTNET
                                           or not config.raw_qty > 0):
            raise ValueError('raw_qty (H2) is a TESTNET-only override of a positive quantity: refused for '
                             f'{config.account.binding.environment}')
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
        if action in OPENING_ACTIONS and not self._lineage_add(action, key, decision_id, intents, subject_id):
            if key is None:                                               # Codex ruling: opening risk is keyed
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

    def _lineage_add(self, action, key, decision_id, intents, subject_id):
        """The one unkeyed opening decision (M4, reported for a Codex ruling): a management plan's ADD of an OPEN lot,
        authorizing exactly the lot's next lineage ADD child (derive_child_intent_id(account, lot, ADD, ordinal)), under
        its child decision id. Its identity is (lot, ADD, journal ordinal), so a replay / restart re-derives the same id
        and G5 refuses a second record: consumed once, like a keyed signal. Grammar G4 forbids a keyed decision from
        authorizing a lineage id, so the plan's add cannot be keyed."""
        if action is not Action.ADD or key is not None or len(intents) != 1:
            return False
        it = intents[0]
        lot = subject_id
        return (it.owner_id == lot and lot is not None and any(x.lot_id == lot for x in self.fold.open_lots())
                and it.intent_id == self.journal.gate().grammar.next_child_intent_id(lot, Purpose.ADD)
                and decision_id == ids.child_decision_id(it.intent_id))

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
        if submit and _is_duplicate(out):
            # a duplicate-id refusal proves the order EXISTS (an earlier send of this same intent landed): never
            # "refused, nothing executed"; read it by its client id and record what the venue holds
            out, submit = self.venue.query(self._ref(iv)), False
        if out.kind is OutcomeKind.KNOWN and out.detail == ALGO_TRIGGERED:
            out = self._follow_child(iv, out)                             # fill truth = fills(child order id)
            if out is None:
                return                                                    # triggered, child not filled yet: wait
        if out.kind is OutcomeKind.KNOWN and out.status not in OPEN_EXCHANGE_STATUSES:
            return                                                        # no open-order record to book: re-query
        try:
            res = result_from(out, iv.intent, ids.result_id(iv.intent_id, len(iv.results)), submit=submit)
        except DomainError as ex:                                         # Cowork h5: e.g. FILLED with executed 0 /
            self._incident(f'{iv.intent_id}: malformed venue answer ({describe(ex)}): taken as UNKNOWN, '
                           're-queried; nothing booked from it')          # half / double: never booked, never raised
            self._durable_incident(ReasonCode.RECONCILE_STALE_READ, 'malformed venue answer taken as UNKNOWN; '
                                   'nothing booked from it', key=iv.intent_id + '/malformed', symbol=iv.intent.symbol,
                                   side=str(iv.intent.side), intents=(iv.intent_id,))
            out = OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=self._ref(iv), observed_at_ms=out.observed_at_ms,
                               detail='malformed')
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

    def _follow_child(self, iv, out):
        """An algo stop that triggered is KNOWN with its CHILD order id (TestnetVenue 'algo_triggered'): it carries no
        executed quantity of its own. Book it only from the child's fills: all of the intent's quantity filled ->
        a FINAL FILLED result at the fills' VWAP under the child id; anything less -> nothing yet (re-queried)."""
        rows, why = rows_of(self.venue.fills(iv.intent.symbol, out.exchange_order_id), symbol=iv.intent.symbol,
                            now=self.now, eoid=out.exchange_order_id, side=str(iv.intent.side))
        if rows is None or not rows:
            return None                                                   # unknown / nothing yet: re-queried
        qty = sum((f.qty for f in rows), ZERO)
        if qty != iv.intent.qty:
            return None
        vwap = RCTX.divide(sum((f.qty * f.price for f in rows), ZERO), qty)
        return OrderOutcome(kind=OutcomeKind.FINAL, ref=out.ref, observed_at_ms=out.observed_at_ms, status='FILLED',
                            exchange_order_id=out.exchange_order_id, executed_qty=qty, avg_price=vwap,
                            detail=ALGO_TRIGGERED)

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
        return permitted(self.mode, self.hold, purpose, op) is Permission.ALLOWED   # the effective mode

    # =============================================================================================== the cycle
    def cycle(self, now_ms, *, decide=True):
        if self.now is not None and now_ms < self.now:
            raise ValueError('the runner clock never goes back')
        self.now = now_ms
        if self._first_now is None:
            self._first_now = now_ms
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
        self._hard_hold_at = self.now if self.now is not None else int(time.time() * 1000)
        why = ex if isinstance(ex, str) else describe(ex)                 # P2: never a raw exception text
        self.hard_hold = f'{d.reason}: {why}'
        self.counters.hard_holds += 1
        self._incident(f'hard HOLD ({d.hold_kind}): {why}')

    def _hard_hold_cycle(self):
        """The A24 emergency set from exchange truth, then a fresh reconciliation and the invariants (counted)."""
        self.counters.hard_hold_cycles += 1
        self._emergency_set()
        rec = self.reconcile()
        self.check_invariants(rec)
        return rec

    # ----------------------------------------------------------------------------------------------- A23 / A24
    def _owned_order(self, client_id):
        """Ours: a journaled client id, an A23 emergency stop, or - when the journal cannot be trusted (a guard) - any
        NEWCORE client id (the zbn1 namespace proves the order was ours)."""
        return (client_id in self.fold.by_client_id or ids.is_emergency_client_id(client_id)
                or (self.guard and ids.is_newcore_client_id(client_id)))

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
        journaled = {(x.symbol, x.side) for x in self.fold.open_lots()}
        journaled |= {(iv.intent.symbol, str(iv.intent.side)) for iv in self.fold.intents.values()
                      if iv.purpose in OPENING_PURPOSES}
        stopped = {(o.ref.symbol, o.position_side) for o in oo.value if self._owned_order(o.ref.client_id)}
        sides = journaled | stopped
        for p in pos.value:
            proven = None
            if p.qty > 0 and (p.symbol, p.side) not in journaled and self.guard:
                # Codex P1-1: with no trusted journal neither an old entry id NOR a resting own stop proves today's
                # position ours; only the surviving net of our own orders (venue trades) does, and at most that
                proven = self._guard_proven(p)
                if proven is None or proven[0] == 'ambiguous':
                    why = proven[1] if proven else 'no own entry of this side in the lookback'
                    if proven is not None or (p.symbol, p.side) in stopped:
                        self._incident(f'guard: {p.symbol} {p.side} {p.qty}: provenance not proven ({why}): '
                                       'nothing added (resting protection kept), HOLD - owner resolves (ambiguous)')
                    continue
            if p.qty <= 0 or ((p.symbol, p.side) not in sides and proven is None):
                continue                                                  # flat, or foreign (A22: an item, untouched)
            covered = sum((o.qty for o in oo.value if o.reduce and o.order_type == 'STOP_MARKET'
                           and (o.ref.symbol, o.position_side) == (p.symbol, p.side)
                           and self._owned_order(o.ref.client_id)), ZERO)
            if proven is not None:
                exposed = min(proven[0], p.qty)                           # at most the proven residual (P1-1)
            else:                                                         # Cowork 6065286201 #1 (A22): our owned
                owned, complete = self._owned_exposure(p.symbol, p.side)  # quantity, never a foreign add
                if not complete:                                          # Codex #13 P1: unknown is not zero and
                    self._incident(f'hard HOLD: {p.symbol} {p.side} {p.qty}: ownership evidence UNREADABLE (fills / '
                                   f'trades unknown): only the proven {owned} may get new protection, nothing new '
                                   'for the rest (it may be foreign); resting protection kept - HOLD, owner')
                exposed = min(p.qty, owned)                               # never widens ownership
            if covered >= exposed:
                continue                                                  # M45: covered: no change
            if proven is not None and proven[1] is None:                  # proven ours, no safe level: escalate
                self._emergency_close(p, exposed - covered)
                continue
            self._emergency_stop(p, exposed - covered, price=None if proven is None else proven[1])
        flat = {(p.symbol, p.side) for p in pos.value if p.qty == 0}
        for o in oo.value:                    # Cowork NEW-3 / G3: OUR stop of a side the venue shows flat (the guard: any
            ours = (self._owned_order(o.ref.client_id) and o.reduce and o.order_type == 'STOP_MARKET') if self.guard \
                else ids.is_emergency_client_id(o.ref.client_id)          # zbn1 stop; else our emergency stops) is
            if ours and (o.ref.symbol, o.position_side) in flat:          # cleanup - never exposed, never foreign
                out = self.venue.cancel(o.ref)                            # is exchange cleanup, never protection
                self._incident(f'hard HOLD: {o.ref.client_id} {o.ref.symbol} {o.position_side} flat: cancelled '
                               f'-> {out.kind}')

    def _owned_exposure(self, symbol, side):
        """Hard HOLD: (proven, complete) - the quantity of (symbol, side) PROVEN ours: the journaled open lots less what
        our emergency orders already closed, plus the venue's fills of our own live opening intents (ENTRY / ADD race or
        partial fills, not yet journaled). Foreign quantity is never in it (A22). Codex #13 P1 (78c0010): evidence that
        cannot be read is UNKNOWN, never zero, and never widens ownership - an unreadable fills read adds nothing
        (complete=False); unknown emergency fills prove none of the lots (proven 0, complete=False)."""
        lots = sum((x.qty for x in self.fold.open_lots() if (x.symbol, x.side) == (symbol, side)), ZERO)
        ef = self._emergency_filled(symbol, side)                         # Cowork 6066239886 #1: already closed
        complete = ef is not None
        owned = ZERO if ef is None else lots - ef
        for iv in self.fold.live_intents():
            if iv.purpose in OPENING_PURPOSES and (iv.intent.symbol, str(iv.intent.side)) == (symbol, side):
                q = self.venue.query(self._ref(iv))
                if q.kind is OutcomeKind.NOT_FOUND or (q.kind is OutcomeKind.FINAL and not q.executed_qty):
                    continue                                              # never reached the venue / filled nothing
                if q.kind is OutcomeKind.FINAL:                           # Cowork 6068372233 #3: the order record
                    rows, why = rows_of(self.venue.fills(symbol, q.exchange_order_id), symbol=symbol, now=self.now,
                                        eoid=q.exchange_order_id, side=side,
                                        expect={q.exchange_order_id: q.executed_qty})
                    if rows is not None:                                  # and its fills AGREE: proven
                        owned += q.executed_qty
                    else:                                                 # disagree in either direction: UNKNOWN
                        complete = False
                        self._incident(f'hard HOLD: {iv.intent_id}: fills disagree with executed qty '
                                       f'{q.executed_qty} ({why}): UNKNOWN, nothing sized from it')
                    continue
                # a WORKING order carries no executed quantity on the port (FINAL only): its fills cannot be checked,
                # so nothing is sized from fills alone - UNKNOWN until it is FINAL (the drain makes it so)
                complete = False
                self._incident(f'hard HOLD: {iv.intent_id}: a working order\'s fills cannot be checked against an '
                               'executed qty: UNKNOWN, nothing sized from them')
        return max(owned, ZERO), complete

    def _emergency_filled(self, symbol, side):
        """What OUR emergency orders (zbn1e stops / closes, never journaled) filled on (symbol, side) since the earliest
        open lot's entry: from the venue's trades, each order matched by its exchange order id to an emergency client
        id re-derived for that order's filled quantity (so it survives a restart).
        Codex #13 P1 / Cowork 6069221337: None (UNKNOWN) whenever the trades evidence is not PROVEN (unreadable,
        stale, short, conflicting, not marked complete): no fallback to this process's own ledger - an emergency order
        it did not send (another process, a previous run) would be invisible to it. Unknown never sizes anything."""
        mem = self._em_filled.get((symbol, side), ZERO)
        lots = [x for x in self.fold.open_lots() if (x.symbol, x.side) == (symbol, side)]
        if not lots:
            return mem
        read = getattr(self.venue, 'trades', None)
        since = min(x.entry.intent.created_at_ms for x in lots)
        tr = read(symbol, side, since) if read is not None else None
        rows, why = rows_of(tr, symbol=symbol, now=self.now, side=side, since=since,
                            expect=self._known_fills(lots)) if tr is not None else (None, 'no trades read')
        if rows is None:                                                  # Cowork 6069221337 (Codex ruling 3 + P1):
            return None                                                   # unproven trades -> ownership UNKNOWN
        groups = {}
        for t in rows:
            groups.setdefault(t.exchange_order_id, []).append(t)
        total = ZERO
        for eoid, grp in groups.items():
            q = sum((t.qty for t in grp), ZERO)
            if self._is_emergency_order(symbol, side, eoid, q):
                total += q
            elif self._emergency_conflict(symbol, side, eoid, grp, q):
                return None                                               # h8 A1c: an id reused - UNKNOWN
        if total < mem:
            return None                                                   # the trades miss a fill we saw: UNKNOWN
        return total

    def _emergency_conflict(self, symbol, side, eoid, grp, qty):
        """Cowork h8 A1c: rows of ONE of our emergency orders that do not add up to it (an exchange order id reused
        by another trade, a row too many): the order is found by an id re-derived for one row's quantity or a running
        total, but its venue record executed a different quantity than the rows say -> conflicting evidence."""
        cands, run = set(), ZERO
        for t in grp:
            run += t.qty
            cands.update((t.qty, run))
        cands.discard(qty)
        for c in sorted(cands):
            if c > 0 and self._is_emergency_order(symbol, side, eoid, c):
                return True
        return False

    def _known_fills(self, lots):
        """{exchange order id: executed} of every fill the journal already holds for these lots (entry, adds, exits):
        a trades window that does not contain them exactly is incomplete (h4 c)."""
        out = {}
        for lot in lots:
            e = lot.entry.final
            if e is not None and e.exchange_order_id and e.executed_qty:
                out[e.exchange_order_id] = out.get(e.exchange_order_id, ZERO) + e.executed_qty
            for f in list(lot.add_fills) + list(lot.closings):
                out[f.exchange_order_id] = out.get(f.exchange_order_id, ZERO) + f.qty
        return out

    def _is_emergency_order(self, symbol, side, eoid, qty):
        def matches(cid, routes):
            known = False
            for route in routes:
                q = self.venue.query(OrderRef(symbol=symbol, client_id=cid, route=route))
                if q.kind in (OutcomeKind.KNOWN, OutcomeKind.FINAL):
                    if q.exchange_order_id == eoid:
                        return True, True
                    known = True
            return False, known
        if matches(ids.emergency_stop_client_id(self.acct, symbol, side, qty, 'close'), ('classic',))[0]:
            return True
        for gen in range(EMERGENCY_GENERATIONS):
            hit, known = matches(ids.emergency_stop_client_id(self.acct, symbol, side, qty, gen), ('classic', 'algo'))
            if hit:
                return True
            if not known:
                return False
        return False

    def _account_emergency_fills(self):
        """Store back (Cowork 6066239886 #1): fills of our emergency orders during a hard HOLD reduced a lot OUTSIDE
        the journal (a zbn1e id cannot be booked as the lot's close). Journaled now as a durable owner item per lot,
        HOLD; the lot's protection is limited to what is still ours (all of it gone: the lot is suspended - no stop /
        close is sent for it; some left: protected at that quantity, never the foreign rest). Checked on the first
        cycle and while the reconciliation shows a position item."""
        if self.hard_hold is not None or not (self.counters.cycles == 1 or (
                self.last_rec is not None and any(k == 'position' for k, _ in self.last_rec.items))):
            return
        sides = {}
        for lot in self.fold.open_lots():
            sides.setdefault((lot.symbol, lot.side), []).append(lot)
        venue = {(p.symbol, p.side): p.qty for p in (self.last_rec.positions or ())} if self.last_rec else {}
        for (sym, side), lots in sides.items():
            ef = self._emergency_filled(sym, side)
            total = sum((x.qty for x in lots), ZERO)
            if ef is None:                                                # Codex #13 P1: unknown is not zero
                if venue.get((sym, side), total) != total:                # and the venue disagrees with the lots
                    new = {x.lot_id for x in lots} - self._own_unknown
                    self._own_unknown.update(x.lot_id for x in lots)
                    if new:
                        self._incident(f'{sym} {side}: lots {total} but the venue holds {venue[(sym, side)]} and our '
                                       'emergency fills cannot be read (trades unknown): ownership UNKNOWN - no new '
                                       'stop / close / resize for these lots, resting protection kept; HOLD, owner')
                        self._durable_incident(ReasonCode.RECONCILE_UNRECONCILED,
                                               f'ownership unknown: lots {total}, venue {venue[(sym, side)]}, our '
                                               'emergency fills unreadable; nothing new sent, owner resolves',
                                               key=','.join(sorted(new)), symbol=sym, side=side, lots=sorted(new))
                    self._hold([ReasonCode.RECONCILE_UNRECONCILED])
                continue
            self._own_unknown.difference_update(x.lot_id for x in lots)
            if ef <= 0:
                continue
            left = max(total - ef, ZERO)
            for lot in lots:
                did = ids.marker_decision_id('emergency_fill', lot.lot_id)
                if self.journal.find_decision(did) is None:
                    self._decision(decision_id=did, action=Action.WAIT, reason=ReasonCode.RECONCILE_UNRECONCILED,
                                   authority=Authority.RECONCILIATION, key=None, symbol=sym, side=side,
                                   subject_id=lot.lot_id,
                                   detail=f'emergency fills {ef} of {sym} {side} during a hard HOLD (outside the '
                                          f'journal): lots {total}, ours left {left}; owner resolves')
                    self._incident(f'{lot.lot_id}: our emergency orders filled {ef} of {sym} {side} during the hard '
                                   f'HOLD (lots {total}, ours left {left}): owner item, HOLD')
                    self._durable_incident(ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE,
                                           f'emergency fills {ef} during a hard HOLD: lots {total}, ours left {left}',
                                           key=lot.lot_id, symbol=sym, side=side, lots=(lot.lot_id,))
            self._hold([ReasonCode.RECONCILE_UNRECONCILED])
            self._em_left[(sym, side)] = left
            if left <= 0:
                self._suspended_lots.update(x.lot_id for x in lots)
            elif len(lots) == 1:
                self._ext_partial[lots[0].lot_id] = min(left, self._ext_partial.get(lots[0].lot_id, left))
                self._resize_down(lots[0].lot_id, self._ext_partial[lots[0].lot_id])

    def _durable_incident(self, kind, detail, *, key, symbol=None, side=None, lots=(), intents=()):
        """Journal an incident (NC-01 r3a IncidentRecorded) once per (kind, key) - only while the store can write (a
        hard HOLD's incidents stay in-process: nothing can be journaled then). `detail` is a fixed template with counts
        and symbols only (never an id, a venue payload or exception text: the record refuses key-shaped text)."""
        if self.hard_hold is not None:
            return
        iid = ids.incident_id(self.acct, str(kind), key)
        if iid in self.fold.incident_ids:
            return
        inc = Incident(incident_id=iid, account_id=self.acct, kind=kind, at_ms=self.now, symbol=symbol,
                       side=None if side is None else Side(side), intent_refs=tuple(intents), lot_refs=tuple(lots),
                       position_refs=(), evidence=(), detail=detail[:160])
        self._emit(IncidentRecorded, reason=kind, incident=inc)

    def _venue_raised(self, name, args, ex):
        at = self.now if self.now is not None else int(time.time() * 1000)
        self._incident(f'venue {name} raised {exc_tag(ex)} - taken as UNKNOWN (adapter contract break)')
        if name in _ORDER_CALLS:
            first = args[0] if args else None
            ref = first if isinstance(first, OrderRef) else getattr(first, 'ref', None)
            return OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=at, detail='raised')
        return ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=at, detail='raised')

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

    def _guard_proven(self, p):
        """Codex P1-1: the surviving net of OUR orders on (symbol, side), from venue facts only. The latest own entry
        the venue confirms by its deterministic client id anchors it; every venue trade of that side since the entry
        fill must belong to an order of ours - the entry, the lot's lineage children (protect / close / reduce / add,
        both routes) or a strategy close re-derived by its key - and the net (opening minus closing) is the proven
        residual. Returns None (no own entry: not ours), ('ambiguous', why) (a trade we cannot attribute, no trades
        read, nothing left of ours) or (residual qty, emergency level | None).
        DISCLOSED LIMIT (Cowork 6065286201 #4): our lot mixed with a foreign same-side add since the entry is
        'ambiguous' - the guard protects nothing on that side (a loud HOLD for the owner), by design (Codex P1-1)."""
        found = self._guard_proven_entry(p)
        if found is None:
            return None
        iid, out, key = found
        net, why = self._surviving_net(p.symbol, p.side, iid, out, key)
        if why is not None:
            return ('ambiguous', why)
        level = self._feasible_level(p, anchor=out.avg_price)
        self._degrade(p, 'guard_fallback_stop' if level is not None else 'emergency_close')
        self._incident(f'guard: {p.symbol} {p.side} proven ours by {out.ref.client_id}: surviving net {net} of '
                       f'{p.qty}; DEGRADED protection: emergency fallback level {level}')
        return (min(net, p.qty), level)

    def _surviving_net(self, symbol, side, iid, out, key):
        """Codex P1-1 / Cowork NEW A: how much of our entry `iid` (venue outcome `out`, decision key `key`) the venue's
        own trades prove still held -> (net, None), or (None, why) when it cannot be correlated: no trades read, the
        entry fills unreadable, a trade of that side since the entry fill that is not ours, or nothing left (net <= 0).
        Ours = the entry, the lot's lineage children (PROTECT / CLOSE / REDUCE / ADD, classic + algo, by deterministic
        id), the keyed strategy closes since the entry, and our zbn1e- emergency orders."""
        p = _Side(symbol, side)
        read = getattr(self.venue, 'trades', None)
        if read is None:
            return None, 'the venue has no trades read'
        fills, why = rows_of(self.venue.fills(symbol, out.exchange_order_id), symbol=symbol, now=self.now,
                             eoid=out.exchange_order_id, side=side, expect={out.exchange_order_id: out.executed_qty})
        if fills is None or not fills:
            return None, f'the entry fills are not proven ({why or "empty"})'
        since = min(f.at_ms for f in fills)
        tr = read(symbol, side, since)
        own = {out.exchange_order_id: 1}
        finals = {out.exchange_order_id: out.executed_qty}               # every FINAL of ours: its rows must be whole
        lot = ids.derive_lot_id(self.acct, iid)
        for purpose in (Purpose.PROTECT, Purpose.CLOSE, Purpose.REDUCE, Purpose.ADD):
            misses = 0
            for n in range(GUARD_CHILDREN):
                child = ids.derive_child_intent_id(self.acct, lot, purpose, n)
                hit = False
                for route in ('classic', 'algo'):
                    q = self.venue.query(OrderRef(symbol=p.symbol, client_id=ids.client_id_for(child, route),
                                                  route=route))
                    if q.kind in (OutcomeKind.KNOWN, OutcomeKind.FINAL) and q.exchange_order_id:
                        own[q.exchange_order_id] = 1 if purpose is Purpose.ADD else -1
                        if q.kind is OutcomeKind.FINAL:
                            finals[q.exchange_order_id] = q.executed_qty
                        hit = True
                misses = 0 if hit else misses + 1
                if misses >= GUARD_CHILD_MISSES:                          # past the lineage (gaps: unsent ordinals)
                    break
        span = (self.now - key.candle_close_ms) // self.cfg.tf_ms - 1     # the candles after the entry's
        wins = self._signal_windows(p.symbol, self.now, min(span, MAX_LOST_TAIL_CANDLES))
        if wins is None:
            return None, 'bars unreadable (strategy closes since the entry)'
        for c, win in wins:
            for s in self.signals.decide(p.symbol, win, c):
                if s.action == CLOSE and s.side == p.side:
                    ck = ids.derive_intent_id(self.acct, self._key(p.symbol, s, Purpose.CLOSE))
                    q = self.venue.query(OrderRef(symbol=p.symbol, client_id=ids.client_id_for(ck, 'classic')))
                    if q.kind in (OutcomeKind.KNOWN, OutcomeKind.FINAL) and q.exchange_order_id:
                        own[q.exchange_order_id] = -1
                        if q.kind is OutcomeKind.FINAL:
                            finals[q.exchange_order_id] = q.executed_qty
        rows, why = rows_of(tr, symbol=symbol, now=self.now, side=side, since=since, expect=finals)
        if rows is None:
            return None, f'trades not proven ({why})'                     # short / stale / mismatched page: UNKNOWN
        net = ZERO
        for t in rows:
            sign = own.get(t.exchange_order_id)
            if sign is None and self._emergency_order_of(p, t):
                sign = -1                                                 # our A23 emergency stop / close filled
            if sign is None:
                return None, f'trade {t.trade_id} of order {t.exchange_order_id} is not attributable to us'
            net = net + t.qty if sign > 0 else net - t.qty
        if net <= 0:
            return None, f'our entry {out.ref.client_id} is fully exited (net {net}): the position is not ours'
        return net, None

    def _emergency_order_of(self, p, t):
        """True when trade t filled one of our emergency orders (zbn1e-: a pure function of symbol, side, qty and a
        generation / 'close'): re-derived for the trade's quantity and confirmed by the venue's own order id."""
        def ours(cid, routes):
            known = False
            for route in routes:
                q = self.venue.query(OrderRef(symbol=p.symbol, client_id=cid, route=route))
                if q.kind in (OutcomeKind.KNOWN, OutcomeKind.FINAL):
                    if q.exchange_order_id == t.exchange_order_id:
                        return True, True
                    known = True
            return False, known
        hit, _ = ours(ids.emergency_stop_client_id(self.acct, p.symbol, p.side, t.qty, 'close'), ('classic',))
        if hit:
            return True
        for gen in range(EMERGENCY_GENERATIONS):                          # generations are used in order
            hit, known = ours(ids.emergency_stop_client_id(self.acct, p.symbol, p.side, t.qty, gen),
                              ('classic', 'algo'))
            if hit:
                return True
            if not known:
                break
        return False

    def _signal_windows(self, symbol, newest_ms, n):
        """Cowork 6066645198 (request cost): the strategy's input window at each of the n + 1 candles newest_ms,
        newest_ms - tf, ... from ONE bars read (limit n + window; a BarSource pages it under its own request bound),
        evaluated locally - never one klines read per candle. Newest first: [(close_ms, bars ending there)]; None when
        the read is not OK. A candle missing from the series is skipped (as a per-candle read would have)."""
        if n < 0:
            return []
        w, tf = self.signals.window, self.cfg.tf_ms
        need, bars, as_of = n + w, [], newest_ms
        while len(bars) < need:                                           # pages of <= BAR_PAGE, newest first:
            r = self.bars.closed_bars(symbol, tf, as_of_ms=as_of,           # ceil((n + window) / 1500) reads
                                      limit=min(BAR_PAGE, need - len(bars)))
            if r.kind is not ReadKind.OK:
                return None
            page = [b for b in r.value if b.close_ms <= as_of]
            bars = page + bars
            if len(page) < min(BAR_PAGE, need - len(bars) + len(page)) or not page:
                break                                                     # the series starts here
            as_of = page[0].close_ms - tf
        lo, out = newest_ms - n * tf, []
        for i in range(len(bars) - 1, -1, -1):
            c = bars[i].close_ms
            if c < lo:
                break
            if c <= newest_ms:
                out.append((c, bars[max(0, i - w + 1):i + 1]))
        return out

    def _guard_proven_entry(self, p):
        """The guard (no trusted journal, Codex tail-loss contract): the LATEST own ENTRY of this side the venue
        confirms FINAL + executed by its deterministic client id - the strategy's own ENTER signals of the last
        GUARD_ENTRY_LOOKBACK candles give the ids (the signals only PROVE the id; no strategy stop is recomputed: the
        journal that held the original stop distance is not trustworthy). Returns (intent id, its query outcome, its
        decision key) or None. It proves only that the entry happened; _guard_proven establishes how much of it
        SURVIVES (Codex P1-1)."""
        if p.symbol not in self.cfg.rules or p.side not in self.cfg.sides or self.now is None:
            return None
        for c, win in self._signal_windows(p.symbol, self.now, GUARD_ENTRY_LOOKBACK) or ():
            for s in self.signals.decide(p.symbol, win, c):
                if s.action != ENTER or s.side != p.side or not s.stop_distance:
                    continue
                iid = ids.derive_intent_id(self.acct, self._key(p.symbol, s, Purpose.ENTRY))
                out = self.venue.query(OrderRef(symbol=p.symbol, client_id=ids.client_id_for(iid, 'classic')))
                if out.kind is OutcomeKind.FINAL and out.executed_qty and out.avg_price:
                    return iid, out, self._key(p.symbol, s, Purpose.ENTRY)       # the LATEST own entry
        return None

    def _emergency_stop(self, p, gap, price=None):
        rules = self.cfg.rules[p.symbol]
        qty = min(rules.quantize_qty(gap, Rounding.DOWN), p.qty)
        if qty <= 0:
            return
        price = price if price is not None else self._emergency_stop_price(p)
        if price is None:
            self._incident(f'hard HOLD: no stop level for {p.symbol} {p.side} {qty}: unprotected, operator needed')
            return
        # (1) narrow queries by the deterministic ids, generation by generation (Cowork F1 / F2): a LIVE one is
        # already in `covered` (that is why a gap is left), an ENDED one is never reused - both move to the next
        # generation; the first id the venue does not know (or cannot answer for) is placed. A restart re-derives the
        # same sequence, so nothing is duplicated.
        for gen in range(EMERGENCY_GENERATIONS):
            cid = ids.emergency_stop_client_id(self.acct, p.symbol, p.side, qty, gen)
            ref = OrderRef(symbol=p.symbol, client_id=cid)
            found = self.venue.query(ref)
            if found.kind not in (OutcomeKind.KNOWN, OutcomeKind.FINAL):
                break
        else:
            self._incident(f'hard HOLD: {EMERGENCY_GENERATIONS} emergency stop ids of {p.symbol} {p.side} {qty} used; '
                           'unprotected gap, operator needed')
            return
        self._em_sent.setdefault((p.symbol, p.side), set()).add((cid, 'classic'))
        out = self.venue.submit_stop(StopOrder(ref=ref, position_side=p.side, qty=qty, stop_price=price))
        if _suggests_algo(out):                                              # same id, the algo route
            ref = OrderRef(symbol=p.symbol, client_id=cid, route='algo')
            self._em_sent[(p.symbol, p.side)].add((cid, 'algo'))
            out = self.venue.submit_stop(StopOrder(ref=ref, position_side=p.side, qty=qty, stop_price=price))
        if _is_duplicate(out):
            out = self.venue.query(ref)
        if out.kind is OutcomeKind.REJECTED and out.error_code == E_WOULD_TRIGGER and F3_POLICY == 'fallback_stop':
            level = self._feasible_level(p)
            if level is not None:
                self._degrade(p, 'fallback_stop')
                self._incident(f'hard HOLD: {cid} @ {price} would trigger; DEGRADED protection: fallback stop {level} '
                               f'(F3, {EMERGENCY_FALLBACK_BUFFER} beyond the current mark)')
                price = level
                out = self.venue.submit_stop(StopOrder(ref=ref, position_side=p.side, qty=qty, stop_price=price))
        if out.kind is OutcomeKind.KNOWN:
            self.counters.emergency_stops += 1
            self._incident(f'hard HOLD emergency stop {cid} {p.symbol} {p.side} {qty} @ {price} -> {out.kind}')
            return
        if out.kind in (OutcomeKind.UNKNOWN, OutcomeKind.ACKNOWLEDGED):   # not terminal: queried / re-sent next cycle
            self._incident(f'hard HOLD emergency stop {cid} {p.symbol} {p.side} {qty} @ {price}: unconfirmed '
                           f'({out.kind}), retried by its id')
            return
        # Codex P1-2: after duplicate / algo routing, EVERY terminal refusal (any code) or a FINAL that is no protection
        # escalates: no emergency stop can be confirmed for this exposed quantity
        self._incident(f'hard HOLD emergency stop {cid} {p.symbol} {p.side} {qty} @ {price} -> {out.kind} '
                       f'{out.error_code}: no protection confirmed')
        self._emergency_close(p, qty)

    def _degrade(self, p, how):
        self.degraded[(p.symbol, p.side)] = how

    def _emergency_close(self, p, qty):
        """Codex F3 ruling: no safe emergency stop could be confirmed - a deterministic reduce-only market close of the
        uncovered quantity (zbn1e id: exchange truth, idempotent; a duplicate means it was already sent)."""
        if hard_hold_permits(Purpose.CLOSE, Op.PLACE, emergency_close=True) is not Permission.ALLOWED:
            self._incident(f'hard HOLD: emergency close of {p.symbol} {p.side} {qty} forbidden by the hard-HOLD table: '
                           'UNPROTECTED, operator needed')
            return
        cid = ids.emergency_stop_client_id(self.acct, p.symbol, p.side, qty, 'close')
        ref = OrderRef(symbol=p.symbol, client_id=cid)
        self._em_sent.setdefault((p.symbol, p.side), set()).add((cid, 'classic'))
        out = self.venue.submit_market(MarketOrder(ref=ref, position_side=p.side, qty=qty, reduce=True))
        if _is_duplicate(out):
            out = self.venue.query(ref)
        if out.kind is OutcomeKind.FINAL and out.executed_qty and cid not in self._em_closed_ids:
            self._em_closed_ids.add(cid)                                  # the in-process floor of the ledger
            k = (p.symbol, p.side)
            self._em_filled[k] = self._em_filled.get(k, ZERO) + out.executed_qty
        self._degrade(p, 'emergency_close')
        self._incident(f'hard HOLD: DEGRADED protection: no safe stop for {p.symbol} {p.side} {qty}: reduce-only '
                       f'emergency close {cid} -> {out.kind}')

    def _current_mark(self, symbol):
        """The venue's current mark (a duck-typed `mark_price(symbol)` read on the venue or the account reads; FakeVenue
        and the testnet adapter have one). None when it cannot be read: no level is then 'safe'."""
        for src in (self.venue, self.reads):
            read = getattr(src, 'mark_price', None)
            if read is not None:
                r = read(symbol)
                if r.kind is ReadKind.OK and r.value and r.value[0] and r.value[0] > 0:
                    return r.value[0]
        return None

    def _feasible_level(self, p, anchor=None):
        """F3 / guard (Codex rulings): a stop level on the protective side of the CURRENT MARK - the mark, the last
        close and `anchor` (an entry fill) whichever is most adverse-side, EMERGENCY_FALLBACK_BUFFER beyond it (below
        for a LONG, above for a SHORT), on the tick and inside the price filter. None when there is no mark."""
        mark = self._current_mark(p.symbol)
        if mark is None:
            return None
        refs = [mark]
        r = self.bars.closed_bars(p.symbol, self.cfg.tf_ms, as_of_ms=self.now, limit=1)
        if r.kind is ReadKind.OK and r.value:
            refs.append(r.value[-1].close)
        if anchor is not None:
            refs.append(anchor)
        rules = self.cfg.rules[p.symbol]
        if p.side == 'LONG':
            level = rules.quantize_price(RCTX.multiply(min(refs), 1 - EMERGENCY_FALLBACK_BUFFER), Rounding.DOWN)
        else:
            level = rules.quantize_price(RCTX.multiply(max(refs), 1 + EMERGENCY_FALLBACK_BUFFER), Rounding.UP)
        try:
            rules.check_price(level, 'emergency_level')
        except Exception:                                                 # outside the price filter: not safe
            return None
        return level

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
        oo = self.venue.open_orders(p.symbol)                             # no journal (a guard): our own resting stop
        if oo.kind is ReadKind.OK:                                        # on that side is the level we chose
            levels = [o.stop_price for o in oo.value if o.reduce and o.order_type == 'STOP_MARKET'
                      and o.position_side == p.side and o.stop_price is not None and self._owned_order(o.ref.client_id)]
            if levels:
                return max(levels) if p.side == 'LONG' else min(levels)  # the bot's own latest (tightest) level
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
        self._orphan_entry_decisions()
        self._recover_lost_entries()
        self._listed = None
        for iv in list(self.fold.live_intents()):
            if iv.state in OPEN_STATES:
                out = self.venue.query(self._ref(iv))
                if out.kind is OutcomeKind.NOT_FOUND and iv.purpose is Purpose.PROTECT and self._is_listed(iv):
                    # LOW-6: the venue LISTS it in its open orders - a lagging by-id lookup proves nothing; the listing
                    # is the venue's own record of a working order
                    o = self._listed[iv.intent.client_order_id]
                    out = OrderOutcome(kind=OutcomeKind.KNOWN, ref=out.ref, observed_at_ms=out.observed_at_ms,
                                       status=o.status, exchange_order_id=o.exchange_order_id)
                self._apply(iv, out, submit=False)
                if iv.state is IntentState.CANCELLING and out.kind is OutcomeKind.KNOWN:
                    self._apply(iv, self.venue.cancel(self._ref(iv)), submit=False)    # the cancel never reached it
                if self._not_found(iv):
                    if iv.purpose in REDUCE_ONLY:
                        self._resend(iv)
                    elif iv.purpose in OPENING_PURPOSES:                 # an entry or a lineage add
                        self._corroborate_entry(iv)
            elif iv.state is IntentState.DURABLE:
                self._send_durable(iv)
        self._release_closed_lot_stops()                                 # after every close this sync resolved

    def _is_listed(self, iv):
        """The order's client id is in the venue's open orders right now (read once per sync)."""
        if self._listed is None:
            oo = self.venue.open_orders()
            self._listed = {o.ref.client_id: o for o in oo.value} if oo.kind is ReadKind.OK else {}
        return iv.intent.client_order_id in self._listed

    @staticmethod
    def _not_found(iv):
        return iv.live and iv.state is IntentState.UNKNOWN and iv.results and \
            iv.results[-1].lookup is Lookup.NOT_FOUND

    def _resend(self, iv):
        """Emergency set (also in HOLD): a reduce-only intent the venue does not know is sent again under the SAME
        deterministic client id. Idempotent: if an earlier send did land, the venue refuses the duplicate id and the
        refusal is read as "exists" (_apply). No new journal 'sent': one intent, one route, the same order."""
        it = iv.intent
        # also when the lot is closed meanwhile (M4: a lost reduce after the stop filled): a reduce-only order can never
        # add exposure, and the venue's refusal (nothing to reduce) is what finally resolves the intent - left alone it
        # would stay UNKNOWN for ever, owned by a lot that no longer exists
        self.counters.resends += 1
        if it.purpose is Purpose.PROTECT:
            out = self.venue.submit_stop(StopOrder(ref=self._ref(iv), position_side=str(it.side), qty=it.qty,
                                                   stop_price=it.stop_price))
        else:
            out = self.venue.submit_market(MarketOrder(ref=self._ref(iv), position_side=str(it.side), qty=it.qty,
                                                       reduce=True))
        submit = True
        if out.kind is OutcomeKind.REJECTED and not _is_duplicate(out):
            # Cowork NEW-3: a refusal of the RE-send proves nothing about the first send (the venue may check the
            # price / position before the id): read the order by its client id before booking anything
            q = self.venue.query(self._ref(iv))
            if q.kind in (OutcomeKind.KNOWN, OutcomeKind.FINAL):
                out, submit = q, False
        if it.purpose is Purpose.PROTECT and submit:
            self._note_refusal(iv, out)
        self._apply(iv, out, submit=submit)

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
        if self.journal.find_decision(did) is None:     # Cowork NEW-1: a crash after the decision, before its result:
            self._decision(decision_id=did, action=Action.RECONCILE, reason=ReasonCode.RECONCILE_MATCH,  # reuse it
                           authority=Authority.RECONCILIATION, key=None, symbol=it.symbol, side=str(it.side),
                           subject_id=iv.intent_id, detail=f'lost entry: {len(reads)} reads, surplus {surplus}')
        adopted = surplus > 0
        res = OrderResult(result_id=ids.result_id(iv.intent_id, len(iv.results)), intent_id=iv.intent_id,
                          account_id=self.acct, client_order_id=it.client_order_id, phase=ResultPhase.FINAL,
                          requested_qty=it.qty, observed_at_ms=self.now, exchange_order_id=None, exchange_status=None,
                          lookup=None, executed_qty=surplus, avg_price=pos.entry_price if adopted else None,
                          evidence=Evidence.POSITION_ADOPTED if adopted else Evidence.NOT_FOUND_CORROBORATED,
                          corroboration=tuple(reads), resolved_by=did, external_trades=(), supersedes_result_id=None)
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
            elif not self._adopt_sent(iv):           # the 20k fuzz 289: its send record may be what the store lost
                self._emit(ResultObserved, reason=ReasonCode.LIFECYCLE_NOT_DURABLE,
                           result=not_sent_result(iv.intent, ids.result_id(iv.intent_id, len(iv.results)), self.now))
                self._state(iv, IntentState.NOT_SENT)
        elif p is Purpose.PROTECT:
            self._send_stop(iv)
        else:
            self._send_close(iv)

    def _adopt_sent(self, iv):
        """An opening intent with no durable send record (the store lost its last events after the venue call): if
        the venue knows its deterministic client id, it WAS sent - record the send, then the venue's answer, and the
        lot is owned and protected like any other. NOT_FOUND (after the visibility window, a cycle later) / UNKNOWN:
        False (the caller decides). The send record is a fact the venue proves, never a guess."""
        out = self.venue.query(self._ref(iv))
        if out.kind not in (OutcomeKind.KNOWN, OutcomeKind.FINAL):
            return False
        self._incident(f'{iv.intent_id}: no durable send record but the venue has {iv.intent.client_order_id} '
                       f'({out.kind}): the send is recorded now')
        self._state(iv, IntentState.SUBMITTED)
        self._apply(iv, out, submit=False)
        if iv.final is not None and iv.executed > 0:
            self._secure(ids.derive_lot_id(self.acct, iv.intent_id))
        return True

    def _orphan_entry_decisions(self):
        """The 20k fuzz 289, intent lost too: an ENTER decision whose derived intent is not recorded. If the venue has
        the derived client id, the order was sent: record the intent, its send and the venue's answer - before the
        reconciliation (Cowork 6062740390 item 6: no HOLD for a position the journal can own). Not found inside its
        candle: _enter re-sends it; after it: asked once per decision per process (NOT_FOUND a cycle later is no lag)."""
        for d in list(self.fold.decisions.values()):
            if d.action is not Action.ENTER or not d.intents or d.decision_id in self._orphans_checked:
                continue
            planned = d.intents[0]
            if planned.intent_id in self.fold.intents or d.key is None:
                continue
            if d.key.candle_close_ms < self.now:
                self._orphans_checked.add(d.decision_id)
            ref = OrderRef(symbol=planned.symbol, client_id=planned.client_order_id,
                           route=route_of(planned.intent_id, planned.client_order_id) or 'classic')
            out = self.venue.query(ref)
            if out.kind in (OutcomeKind.KNOWN, OutcomeKind.FINAL):
                self._incident(f'{planned.intent_id}: no durable intent record but the venue has '
                               f'{planned.client_order_id}: recorded now')
                iv = self._record_durable(planned)
                self._adopt_sent(iv)

    def _recover_lost_entries(self):
        """The 20k fuzz 289 with EVERY record of the entry lost (lazy store, 3 events): a venue position the journal
        cannot explain on a side this runner trades. The strategy's own ENTER signals of the last GUARD_ENTRY_LOOKBACK
        candles give the deterministic client ids it would have used; if the venue has one of them, the order is
        ours (exchange truth, not inference): its decision, intent, send and result are recorded and the lot is owned
        and protected like any other. Checked on the first cycle of a process and after a position mismatch; each
        candidate once per process. Nothing matches -> the reconciliation's untracked-position HOLD (owner item)."""
        if not (self.counters.cycles == 1 or (self.last_rec is not None and
                                                any(k == 'position' for k, _ in self.last_rec.items))):
            return
        pos = self.venue.positions()
        if pos.kind is not ReadKind.OK:
            return
        complete = True
        owned = {}
        for x in self.fold.open_lots():
            owned[(x.symbol, x.side)] = owned.get((x.symbol, x.side), ZERO) + x.qty
        for p in pos.value:
            if p.symbol not in self.cfg.symbols or p.side not in self.cfg.sides or \
                    p.qty <= owned.get((p.symbol, p.side), ZERO) or self.fold.live_entries(p.symbol, p.side):
                continue
            wins = self._signal_windows(p.symbol, self.now, self._lost_tail_candles())
            if wins is None:
                complete = False                                          # retried; the deep search stays armed
                continue
            for c, win in wins:                                           # newest first (Cowork NEW A)
                if (p.symbol, p.side, c) in self._lost_checked:
                    continue
                self._lost_checked.add((p.symbol, p.side, c))
                if self._recover_entry_at(p.symbol, p.side, c, win):
                    break
        if complete:
            self._deep_search_done = True                                 # later mismatches: the shallow search

    def _lost_tail_candles(self):
        """How far back a lost entry can be (Cowork NEW A, beyond 60): a lost tail is a SUFFIX of the journal, so an entry
        whose every record was lost was decided after the journal's last durable event at boot. The search reaches that
        event's candle (+1 candle margin), at least GUARD_ENTRY_LOOKBACK, at most MAX_LOST_TAIL_CANDLES (beyond: an
        incident; the untracked-position HOLD stays for the owner). Bounded by the lost tail, not by wall-clock time.
        Only until ONE boot search completed (Cowork 6066645198): later position mismatches (a manual position after
        hours of uptime) search LOST_ENTRY_LOOKBACK candles only - a tail is lost at a restart, never mid-run."""
        if self._deep_search_done:
            return LOST_ENTRY_LOOKBACK                                    # mid-run (a foreign position...): shallow
        if self._boot_last_at is None or self.now is None:
            return GUARD_ENTRY_LOOKBACK
        n = max(GUARD_ENTRY_LOOKBACK, -(-(self.now - self._boot_last_at) // self.cfg.tf_ms) + 1)
        if n > MAX_LOST_TAIL_CANDLES:
            self._incident(f'lost-tail search: the last durable event is {n} candles back, beyond '
                           f'MAX_LOST_TAIL_CANDLES={MAX_LOST_TAIL_CANDLES}: searched that far only (owner item)')
            n = MAX_LOST_TAIL_CANDLES
        return n

    def _recover_entry_at(self, symbol, side, close_ms, bars):
        for s in self.signals.decide(symbol, bars, close_ms):
            if s.action != ENTER or s.side != side:
                continue
            key = self._key(symbol, s, Purpose.ENTRY)
            did = ids.derive_decision_id(self.acct, key)
            if self.journal.find_decision(did) is not None:
                return False                                              # its decision survived: the orphan path
            iid = ids.derive_intent_id(self.acct, key)
            ref = OrderRef(symbol=symbol, client_id=ids.client_id_for(iid, 'classic'))
            out = self.venue.query(ref)
            if out.kind is not OutcomeKind.FINAL or not out.executed_qty:
                continue
            net, why = self._surviving_net(symbol, side, iid, out, key)     # Codex P1-1 / Cowork NEW A + B
            if why is not None or net != out.executed_qty:
                self._incident(f'{iid}: the venue has our entry {ref.client_id} (executed {out.executed_qty}) but its '
                               f'survival is not proven ({why or f"surviving net {net}"}): nothing adopted, HOLD - '
                               'owner resolves (ambiguous)')
                return True                                               # the latest own entry decides: no older one
            self._incident(f'{iid}: every record lost but the venue has {ref.client_id} (executed '
                           f'{out.executed_qty}): decision, intent and result recorded now')
            planned = planned_intent(intent_id=iid, account_id=self.acct, decision_id=did, purpose='entry',
                                     symbol=symbol, side=side, qty=out.executed_qty, reason=s.reason, at_ms=self.now,
                                     slot_id=self.cfg.slot_id)
            self._decision(decision_id=did, action=Action.ENTER, reason=s.reason, authority=Authority.STRATEGY,
                           key=key, symbol=symbol, side=side, intents=(planned,),
                           detail=f'recovered after a lost journal tail dist {s.stop_distance}')
            self._dist[iid] = s.stop_distance
            return self._adopt_sent(self._record_durable(planned))
        return False

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
            if iv.purpose in (Purpose.ENTRY, Purpose.ADD, Purpose.CLOSE, Purpose.REDUCE) and iv.state in OPEN_STATES:
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
        self._detect_external_flats()
        for lot in self.fold.open_lots():
            if self._suspended(lot.lot_id):
                continue
            d = self.fold.pending_closes.get(lot.lot_id)
            if d is not None and lot.closing is None:                     # a decided close a crash interrupted:
                self._close_lot(lot, reason=d.reason, key=d.key)          # finish it (never a fresh stop first)
            self._secure(lot.lot_id)
        self._escalate_unconfirmed()

    def _escalate_unconfirmed(self):
        """Cowork NEW-4: a stop that stays unconfirmed (sent, its answer unknown / never landing) for ESCALATE_AFTER
        consecutive cycles is not protection: the lot is closed with a reduce-only market order (exit.stop_failed),
        the stop staying live meanwhile (ruling 13). Outside hard HOLD only (no journal: A24 forbids the close)."""
        seen = set()
        for lot in self.fold.open_lots():
            if not self._runner_owns(lot.lot_id) or self._suspended(lot.lot_id):
                continue
            stop = lot.live_stop
            if stop is None or stop.state is IntentState.WORKING or lot.closing is not None:
                self._unconfirmed.pop(lot.lot_id, None)
                continue
            seen.add(lot.lot_id)
            at, n = self._unconfirmed.get(lot.lot_id, (None, 0))
            if at != self.now:
                n += 1
                self._unconfirmed[lot.lot_id] = (self.now, n)
            if n >= ESCALATE_AFTER and self.hard_hold is None:
                self._incident(f'{lot.lot_id}: stop {stop.intent_id} unconfirmed for {n} cycles: reduce-only close')
                self._close_lot(lot, reason=ReasonCode.EXIT_STOP_FAILED, key=None)
        for k in [k for k in self._unconfirmed if k not in seen]:
            self._unconfirmed.pop(k, None)

    def _lot(self, lot_id):
        return next((x for x in self.fold.open_lots() if x.lot_id == lot_id), None)

    def _secure(self, lot_id):
        if self._suspended(lot_id):
            return                                                        # MED-3: the owner resolves it
        self._secure_lot(lot_id)

    def _secure_lot(self, lot_id):
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
        """The stop distance of an entry: cached; else the JOURNALED planned distance (H1: the ENTER decision's
        'dist <d>' token, durable since S1 - no NC-01 field carries it, proposed for r3); else re-derived from the
        klines at its key candle (exchange truth: an adopted fill / an older journal)."""
        d = self._dist.get(entry_iv.intent_id)
        if d is not None:
            return d
        d = journaled_distance(self.fold.decisions.get(entry_iv.intent.decision_id))
        if d is not None:
            self._dist[entry_iv.intent_id] = d
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
            return rules.quantize_price(lot.entry_price - d, Rounding.DOWN)       # never tighter than the R
        return rules.quantize_price(lot.entry_price + d, Rounding.UP)

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
        return 'algo' if code in ('unknown', ALGO_ROUTE) else 'classic'

    def _protect(self, lot, *, replacing=False):
        if not lot.open or (lot.live_stop is not None and not replacing) or lot.closing is not None:
            return
        if lot.lot_id in self._own_unknown:
            return                                                        # Codex #13 P1: it may be foreign now
        if not self._permits(Purpose.PROTECT, Op.PLACE):
            return
        n = len(lot.protects)
        iid = self.journal.gate().grammar.next_child_intent_id(lot.lot_id, Purpose.PROTECT)    # read just before
        did = ids.child_decision_id(iid)
        prior = self.journal.find_decision(did)
        if prior is not None:                                             # crash gap: decided, intent not recorded
            planned = prior.decision.intents[0]
        else:
            price = self._protect_price(lot)
            route = self._next_route(lot)
            reason = ReasonCode.PROTECT_PLACE if n == 0 or route == 'algo' else ReasonCode.PROTECT_RESTORING
            qty = self._protect_qty(lot)
            planned = planned_intent(intent_id=iid, account_id=self.acct, decision_id=did, purpose='protect',
                                     symbol=lot.symbol, side=lot.side, qty=qty, reason=reason, at_ms=self.now,
                                     owner_id=lot.lot_id, stop_price=price, route=route)
            self._decision(decision_id=did, action=Action.PROTECT, reason=reason, authority=Authority.PROTECTION,
                           key=None, symbol=lot.symbol, side=lot.side, intents=(planned,), subject_id=lot.lot_id,
                           evidence=(lot.entry.intent_id,), detail=f'stop {price} x {qty}')
        self._send_stop(self._record_durable(planned))

    def _protect_price(self, lot):
        """The level a runner stop protects the lot at (hook: a lot management handed back keeps its plan's level)."""
        return self.stop_price_of(lot)

    def _send_stop(self, iv):
        self._state(iv, IntentState.SUBMITTED)
        it = iv.intent
        out = self.venue.submit_stop(StopOrder(ref=self._ref(iv), position_side=str(it.side), qty=it.qty,
                                               stop_price=it.stop_price))
        self._note_refusal(iv, out)
        self._apply(iv, out, submit=True)
        self._count_reduce_refusal(iv, out)
        self._resolve(iv)                                                 # a refused stop is handled by _secure

    def _note_refusal(self, iv, out):
        """The venue's refusal code of a stop attempt (in memory: NC-01 results carry no error code)."""
        if out.kind is OutcomeKind.REJECTED and not _is_duplicate(out):
            self._refusals[iv.intent_id] = ALGO_ROUTE if _suggests_algo(out) else out.error_code

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
        for s in self._signals_now(symbol, bars):
            if s.side not in self.cfg.sides:
                continue
            if s.action == CLOSE:
                self._exit(symbol, s)
            elif s.action == ENTER:
                self._enter(symbol, s, bars[-1].close)

    def _signals_now(self, symbol, bars):
        """The strategy's signals for the candle that JUST closed (Cowork LOW, strategy contract: decide() has no
        freshness check of its own). A signal of any other candle is ignored with an incident, never acted on."""
        out = []
        for s in self.signals.decide(symbol, bars, self.now):
            if s.candle_close_ms != self.now:
                self._incident(f'stale signal ignored: {symbol} {s.action} {s.side} for candle {s.candle_close_ms}, '
                               f'now {self.now}')
                continue
            out.append(s)
        return tuple(out)

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
                if self.cfg.raw_qty is not None:                          # H2: the venue, not the sizer, refuses
                    gate = None
        if gate is not None:
            self.counters.skips += 1
            self._decision(decision_id=did, action=Action.SKIP, reason=gate, authority=Authority.STRATEGY, key=key,
                           symbol=symbol, side=s.side, detail=f'skip {gate}')
            return
        iid = ids.derive_intent_id(self.acct, key)
        planned = planned_intent(intent_id=iid, account_id=self.acct, decision_id=did, purpose='entry', symbol=symbol,
                                 side=s.side, qty=sz.qty if self.cfg.raw_qty is None else self.cfg.raw_qty,
                                 reason=s.reason, at_ms=self.now, slot_id=self.cfg.slot_id)
        raw = '' if self.cfg.raw_qty is None else f' raw_qty {self.cfg.raw_qty}'
        self._decision(decision_id=did, action=Action.ENTER, reason=s.reason, authority=Authority.STRATEGY, key=key,
                       symbol=symbol, side=s.side, intents=(planned,),
                       detail=f'risk {sz.risk_usd:.8f} dist {s.stop_distance} capped {sz.capped}{raw}')
        self._dist[iid] = s.stop_distance
        self.counters.entries += 1
        self._send_entry(self._record_durable(planned))

    def _entry_gate(self, symbol, side):
        if self.mode is not EntriesMode.ACTIVE:
            return GATE_REASON[self.mode]
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
        """A reduce-only market close of the whole lot WHILE its stop stays live (Codex ruling 13, TNET N6: never cancel
        protection before the exit; hedge mode + reduce-only make both live safe - whichever fills first, the other
        cannot over-fill). The leftover stop is cancelled only once the close's FINAL result proves the lot flat
        (_release_closed_lot_stops, also at every sync, so a crash in between is finished by the next cycle).
        key=None: an unkeyed (protection) close."""
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
        if self._lot(lot.lot_id) is None:
            return                                                        # the stop filled first: nothing to close
        self._send_close(self._record_durable(planned))                   # the stop stays live meanwhile
        self._release_closed_lot_stops()

    def _count_reduce_refusal(self, iv, out):
        """MED-3: a lot whose reduce-only sends keep being refused as 'nothing to reduce' may have been closed OUTSIDE
        the bot. After REDUCE_REFUSALS in a row the venue position is read: flat on that side -> one durable owner item
        ('external close suspected') + HOLD, and the lot gets no more sends (until the owner / REC-02 resolves it)."""
        lot_id = iv.intent.owner_id
        if lot_id is None:
            return
        if out.kind is OutcomeKind.REJECTED and out.error_code in EXTERNAL_CLOSE_CODES:
            n = self._reduce_refused.get(lot_id, 0) + 1
            self._reduce_refused[lot_id] = n
            if n >= REDUCE_REFUSALS:
                self._check_external_close(lot_id, iv.intent.symbol, str(iv.intent.side))
        elif out.kind in (OutcomeKind.KNOWN, OutcomeKind.FINAL):
            self._reduce_refused.pop(lot_id, None)

    def _detect_external_flats(self):
        """Cowork p3: an open lot whose side the venue reads FLAT (this cycle's reconciliation) was closed outside the
        bot - unless a fill of our own is in flight: the lot's live orders are re-read first, so our own stop / close
        fill is booked. Still open and flat -> the external-close owner item, before anything is sent for the lot."""
        self._account_emergency_fills()
        rec = self.last_rec
        if rec is None or rec.positions is None or self.hard_hold is not None:
            return
        listed = {p.symbol for p in rec.positions}
        qty = {(p.symbol, p.side): p.qty for p in rec.positions}
        for lot in list(self.fold.open_lots()):                           # Cowork 6065286201 #2: an external-close
            if self._suspended(lot.lot_id) and lot.symbol in listed \
                    and qty.get((lot.symbol, lot.side), ZERO) == 0 and any(p.live for p in lot.protects):
                self._release_flat_side(lot)                              # lot keeps no stop resting on a flat side
        self._detect_external_partials(listed, qty)
        resting = {o.ref.client_id for o in (rec.orders or ()) if o.status in OPEN_EXCHANGE_STATUSES}
        for lot in list(self.fold.open_lots()):
            if lot.symbol not in listed or qty.get((lot.symbol, lot.side), ZERO) > 0 or self._suspended(lot.lot_id):
                continue
            for iv in [x for x in self.fold.live_intents() if x.intent.owner_id == lot.lot_id]:
                self._apply(iv, self.venue.query(self._ref(iv)), submit=False)
            if self._lot(lot.lot_id) is None:
                continue                                                  # our own fill explained it
            # an order of ours that may still deliver the fill (a triggered algo stop whose child is not readable yet,
            # an unknown answer, a close in flight) explains the flat side for now: re-checked next cycle. Only a stop
            # still RESTING untriggered (it did not fire) or no live order at all is an external close.
            if any(not (iv.purpose is Purpose.PROTECT and iv.state is IntentState.WORKING
                        and iv.intent.client_order_id in resting)
                   for iv in self.fold.live_intents() if iv.intent.owner_id == lot.lot_id):
                continue
            self._check_external_close(lot.lot_id, lot.symbol, lot.side)

    def _check_external_close(self, lot_id, symbol, side):
        if self._suspended(lot_id):
            return
        r = self.venue.positions(symbol)
        if r.kind is not ReadKind.OK or any(p.side == side and p.qty > 0 for p in r.value):
            return                                                        # not proven flat: the normal paths go on
        did = ids.marker_decision_id('external_close', lot_id)
        if self.journal.find_decision(did) is None:
            self._decision(decision_id=did, action=Action.WAIT, reason=ReasonCode.RECONCILE_UNRECONCILED,
                           authority=Authority.RECONCILIATION, key=None, symbol=symbol, side=side, subject_id=lot_id,
                           detail=f'external close suspected: venue flat, lot {lot_id} open; owner resolves')
        self._suspended_lots.add(lot_id)
        self._durable_incident(ReasonCode.RECONCILE_MANUAL_CLOSE, 'external close suspected: venue flat, lot open; '
                               'no more sends, owner resolves', key=lot_id, symbol=symbol, side=side, lots=(lot_id,))
        self._incident(f'{lot_id}: external close suspected (venue flat on {symbol} {side}, '
                       f'{self._reduce_refused.get(lot_id, 0)} reduce-only sends refused): owner item, no more sends')
        self._hold([ReasonCode.RECONCILE_UNRECONCILED])
        lot = self._lot(lot_id)
        if lot is not None:
            self._release_flat_side(lot)

    # The two narrow cases in which the runner cancels a PROTECT order of its own while HOLD (NORMAL) - which the
    # central table otherwise forbids (modes: protective orders are kept). Neither removes protection from exposure:
    #   flat_side  the venue side was just re-read FLAT (Cowork NEW-3 / 6065286201 #2): the stop protects nothing;
    #   replaced   a smaller replacement stop of the same lot is confirmed WORKING first (6065286201 #3: re-sized
    #              down after an external partial close).
    # Codified here only; the central cell is a proposed NC-01 / NC-02a change (same pattern as P1-3).
    def _release_flat_side(self, lot):
        r = self.venue.positions(lot.symbol)
        if r.kind is not ReadKind.OK or any(p.side == lot.side and p.qty > 0 for p in r.value):
            return                                                        # not proven flat: protection is kept
        for stop in [p for p in lot.protects if p.live]:
            self._release_stop(stop, f'venue {lot.symbol} {lot.side} flat (external close)', 'flat_side')

    def protect_cancel_in_hold(self, kind, stop):
        """THE single check for cancelling a PROTECT order of ours while the central table forbids it (HOLD NORMAL:
        protective orders are kept). Allowed in exactly two cases, each re-proven here from venue truth:
          'flat_side'  the venue side of the stop's lot is re-read FLAT now: the stop protects nothing
                       (Cowork NEW-3 / 6065286201 #2, an external flat close);
          'replaced'   a smaller replacement stop of the same lot is WORKING (Cowork 6065286201 #3, re-sized down after
                       an external partial close): protection is never removed before its replacement is confirmed.
        Pending Codex's ruling (asked on #13) this is where the central NC-01 / NC-02a cell gets wired in."""
        if self._permits(Purpose.PROTECT, Op.CANCEL):
            return True                                                   # the table allows it (not HOLD)
        it = stop.intent
        if kind == 'flat_side':
            r = self.venue.positions(it.symbol)
            return r.kind is ReadKind.OK and not any(p.side == str(it.side) and p.qty > 0 for p in r.value)
        if kind == 'replaced':
            lot = self._lot(it.owner_id)
            return lot is not None and any(p.live and p.intent_id != stop.intent_id and p.intent.qty < it.qty
                                           and p.state is IntentState.WORKING for p in lot.protects)
        return False

    def _release_stop(self, stop, why, kind):
        if not self.protect_cancel_in_hold(kind, stop):
            self._incident(f'{stop.intent_id}: release ({why}) not permitted ({kind}): protection kept')
            return
        if stop.state is not IntentState.CANCELLING:
            self._state(stop, IntentState.CANCELLING, ReasonCode.LIFECYCLE_ORPHAN_CANCEL)
        out = self.venue.cancel(self._ref(stop))
        self._apply(stop, out, submit=False)
        if stop.live:
            self._apply(stop, self.venue.query(self._ref(stop)), submit=False)
        self._incident(f'{stop.intent_id}: our stop {stop.intent.client_order_id} released ({why}) -> '
                       f'{"cancelled" if not stop.live else stop.state}')

    def _detect_external_partials(self, listed, qty):
        """Cowork 6065286201 #3: the venue holds LESS than our open lots of a side with no fill of ours in flight (the
        lot's live orders are re-read first, so our own reduce / stop fill is booked) -> a durable 'external partial
        close suspected' owner item, HOLD, and the lot's protection re-sized DOWN to the venue quantity (one lot per
        side; with several the attribution is ambiguous: the item and HOLD only, protection unchanged)."""
        sides = {}
        for lot in self.fold.open_lots():
            if not self._suspended(lot.lot_id) and lot.lot_id not in self._own_unknown:
                sides.setdefault((lot.symbol, lot.side), []).append(lot)
        for (sym, side), lots in sides.items():
            v = qty.get((sym, side), ZERO)
            ours = sum((x.qty for x in lots), ZERO) - self._em_filled_known(sym, side)
            if sym not in listed or v <= 0 or v >= ours:
                continue
            for lot in lots:
                for iv in [x for x in self.fold.live_intents() if x.intent.owner_id == lot.lot_id]:
                    self._apply(iv, self.venue.query(self._ref(iv)), submit=False)
            lots = [x for x in self.fold.open_lots() if (x.symbol, x.side) == (sym, side)]
            if not lots or any(iv.purpose is not Purpose.PROTECT for iv in self.fold.live_intents()
                               if iv.intent.owner_id in {x.lot_id for x in lots}):
                continue                                                  # ours in flight: re-checked next cycle
            r = self.venue.positions(sym)
            if r.kind is not ReadKind.OK:
                continue
            v = sum((p.qty for p in r.value if p.side == side), ZERO)
            total = sum((x.qty for x in lots), ZERO) - self._em_filled_known(sym, side)
            if v <= 0 or v >= total:
                continue
            for lot in lots:
                did = ids.marker_decision_id('external_partial', lot.lot_id)
                if self.journal.find_decision(did) is None:
                    self._decision(decision_id=did, action=Action.WAIT, reason=ReasonCode.RECONCILE_UNRECONCILED,
                                   authority=Authority.RECONCILIATION, key=None, symbol=sym, side=side,
                                   subject_id=lot.lot_id,
                                   detail=f'external partial close suspected: venue {v} < lots {total}, lot '
                                          f'{lot.lot_id} {lot.qty}; owner resolves')
                    self._incident(f'{lot.lot_id}: external partial close suspected (venue {sym} {side} {v} < our '
                                   f'lots {total}, no fill of ours): owner item, protection re-sized down')
                    self._durable_incident(ReasonCode.RECONCILE_MANUAL_CLOSE,
                                           f'external partial close suspected: venue {v} < lots {total}; protection '
                                           're-sized down, owner resolves', key=lot.lot_id + '/partial', symbol=sym,
                                           side=side, lots=(lot.lot_id,))
            self._hold([ReasonCode.RECONCILE_UNRECONCILED])
            if len(lots) == 1:
                self._ext_partial[lots[0].lot_id] = v
                self._resize_down(lots[0].lot_id, v)
            else:
                self._incident(f'{sym} {side}: external partial close over {len(lots)} lots: attribution ambiguous, '
                               'protection unchanged (owner resolves)')

    def _em_filled_known(self, symbol, side):
        """Our emergency fills already accounted this process (store back) - no venue read."""
        left = self._em_left.get((symbol, side))
        if left is None:
            return self._em_filled.get((symbol, side), ZERO)
        return sum((x.qty for x in self.fold.open_lots() if (x.symbol, x.side) == (symbol, side)), ZERO) - left

    def _protect_qty(self, lot):
        """The quantity a runner stop protects: the lot, or less after an external partial close (never above it)."""
        v = self._ext_partial.get(lot.lot_id)
        return lot.qty if v is None else min(lot.qty, v)

    def _resize_down(self, lot_id, target):
        """Replace the lot's stop by one of `target` (< its qty): the smaller stop is placed and confirmed WORKING
        BEFORE the old one is released; a refused / unconfirmed replacement leaves the old stop in place."""
        lot = self._lot(lot_id)
        if lot is None or lot.closing is not None:
            return
        live = [p for p in lot.protects if p.live]
        if not live:
            return                                                        # _secure protects at _protect_qty
        keep = next((p for p in reversed(live) if p.intent.qty == target and p.state is IntentState.WORKING), None)
        if keep is None:
            if any(p.intent.qty == target for p in live):
                return                                                    # the replacement is unconfirmed yet
            self._protect(lot, replacing=True)
            lot = self._lot(lot_id)
            keep = next((p for p in lot.protects if p.live and p.intent.qty == target
                         and p.state is IntentState.WORKING), None)
            if keep is None:
                return                                                    # old stop kept: retried next cycle
        for stop in [p for p in self._lot(lot_id).protects if p.live and p is not keep
                     and p.intent_id != keep.intent_id]:
            self._release_stop(stop, f'replaced by {keep.intent.client_order_id} x {target} (external partial close)',
                               'replaced')

    def _suspended(self, lot_id):
        """A lot with a durable external-close owner item: no stop / close is sent for it any more."""
        if lot_id in self._suspended_lots:
            return True
        if self.journal.find_decision(ids.marker_decision_id('external_close', lot_id)) is not None:
            self._suspended_lots.add(lot_id)
            return True
        return False

    def _runner_owns(self, lot_id):
        """True when the runner itself manages the lot's protection (hook: a management driver owns its own lots)."""
        return True

    def _release_closed_lot_stops(self):
        """Cancel the live stops of lots the exchange has proven flat (a close / reduce FINAL booked the whole lot):
        protection is released only AFTER the exit, never before (Codex ruling 13). Runs after every close and at every
        sync (a crash between the close's result and the cancel is finished here)."""
        for lot in self.fold.lots():
            if lot.open or not self._runner_owns(lot.lot_id):            # a managed lot: the driver releases it
                continue
            for stop in [p for p in lot.protects if p.live]:
                if stop.state is not IntentState.CANCELLING:
                    self._state(stop, IntentState.CANCELLING, ReasonCode.LIFECYCLE_ORPHAN_CANCEL)
                out = self.venue.cancel(self._ref(stop))
                self._apply(stop, out, submit=False)
                if stop.live:
                    self._apply(stop, self.venue.query(self._ref(stop)), submit=False)

    def _send_close(self, iv):
        self._state(iv, IntentState.SUBMITTED)
        it = iv.intent
        out = self.venue.submit_market(MarketOrder(ref=self._ref(iv), position_side=str(it.side), qty=it.qty,
                                                   reduce=True))
        self._apply(iv, out, submit=True)
        self._count_reduce_refusal(iv, out)
        if iv.live:
            self._hold([ReasonCode.EXEC_ORDER_FAILED], reason=ReasonCode.EXEC_ORDER_FAILED)
            self._resolve(iv)                                             # a refused close is handled by _secure

    # ----------------------------------------------------------------------------------------------- operator
    def resume(self, now_ms):
        """Leave HOLD: needs a clean fresh reconciliation and records an operator RESUME decision. Never from a hard
        HOLD (Cowork F4): only a restart with a writable store leaves it."""
        if self.hard_hold is not None:
            return False
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
        except EvidencePending as ex:                                     # not a breach: evidence pending
            if str(ex) not in self._pending_evidence:
                self._pending_evidence.add(str(ex))
                self._incident(f'projection pending: {ex} (never a zero fee)')
        except Exception as ex:                                           # NC-01 invariant refused the state
            problems.append(f'I3 {describe(ex, (ValueError,))}')           # NC-01 constructors over OUR records
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
            elif pf is None:                       # I3 refused the projection (reported above): I1 still measures the
                for lot in self.fold.open_lots():  # same thing from the fold - the WORKING carrier, listed now
                    c = lot.carrier
                    if c is not None and c.state is IntentState.WORKING and c.intent.client_order_id in listed:
                        cover[(lot.symbol, lot.side)] = cover.get((lot.symbol, lot.side), ZERO) + c.intent.qty
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
            i1 = [p for p in problems if p.startswith('I1')]
            naked = bool(i1)
            self.counters.unprotected_cycles += naked
            if naked:                                      # an incident on EVERY naked cycle (Cowork NEW-4)
                self._incident('; '.join(i1))
            if self.fold.mode is not EntriesMode.HOLD and self.hard_hold is None:
                if naked:                                  # never ACTIVE while naked: stop new risk at once (durable)
                    self._hold([ReasonCode.PROTECT_CHECKING], reason=ReasonCode.PROTECT_CHECKING)
                # strict: a breach - except a naked side whose stop is IN FLIGHT (sent, answer unknown) now that the
                # HOLD is durable: the bounded window the protect / escalation path is closing (Cowork LOW)
                inflight = {(x.symbol, x.side) for x in self.fold.open_lots() if x.live_stop is not None
                            and x.live_stop.state in (IntentState.SUBMITTED, IntentState.UNKNOWN)}
                excused = naked and len(i1) == len(problems) and self.fold.mode is EntriesMode.HOLD and all(
                    any(f'I1 {s} {sd}:' in p for s, sd in inflight) for p in i1)
                if self.cfg.strict and not excused:
                    raise InvariantBreach('; '.join(problems))
        return problems

    # ----------------------------------------------------------------------------------------------- projections
    def _fee(self, symbol, eoid, qty=None):
        """A fill fee of the projection, from PROVEN rows only (Cowork 6068372233 #5): unproven -> EvidencePending
        (the projection is pending, with an incident), never ZERO."""
        rows, why = rows_of(self.venue.fills(symbol, eoid), symbol=symbol, now=self.now, eoid=eoid,
                            expect=None if qty is None else {eoid: qty})
        if rows is None:
            raise EvidencePending(f'fee of order {eoid}: {why}')
        return sum((f.fee for f in rows), ZERO)

    def portfolio(self, rec=None):
        """The NC-01 Portfolio this fold + reconciliation proves (constructing it runs every NC-01 invariant).
        Raises fill_evidence.EvidencePending (a LookupError) when a fill fee it needs is not PROVEN by the venue
        (never a zero): check_invariants records it as a pending incident, summary() reports ownership None; a direct
        caller must handle it the same way (the projection is pending, not refused)."""
        rec = rec or self.last_rec
        f = self.fold
        mode, hold = self.mode, self.hold                                 # Cowork h7: the EFFECTIVE mode
        if self.hard_hold is not None:                                    # (the journal cannot hold this HOLD)
            reasons = tuple(dict.fromkeys((durability_hold().reason,) + tuple(f.mode_reasons or ())))
            since = self._hard_hold_at
        else:
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
            stop_iv = lot.carrier                                         # = live_stop unless a replacement flies
            repl = lot.replacement
            price = stop_iv.intent.stop_price if stop_iv is not None else self.stop_price_of(lot)
            qty = stop_iv.intent.qty if stop_iv is not None else lot.qty
            confirmed = rec.at_ms if (stop_iv is not None and stop_iv.state is IntentState.WORKING and
                                      stop_iv.intent.client_order_id in listed) else None
            prot = Protection(owner_id=lot.lot_id, price=price, qty=qty,
                              order=None if stop_iv is None else stop_iv.intent_id,
                              replacement=None if repl is None else repl.intent_id,
                              confirmed_at_ms=confirmed, miss=None)
            e = lot.entry.final
            fills = [Fill(at_ms=lot.opened_at_ms, reason=lot.entry.intent.reason, qty=lot.initial_qty,
                          price=lot.entry_price, fee=self._fee(lot.symbol, e.exchange_order_id, e.executed_qty),
                          result_id=e.result_id,
                          decision_id=None)]
            per_order = {}
            for c, _ in lot.ledger():
                per_order[c.exchange_order_id] = per_order.get(c.exchange_order_id, ZERO) + c.qty
            for c, _ in lot.ledger():                                     # adds (opening) and closings, time order
                fills.append(Fill(at_ms=c.at_ms, reason=c.reason, qty=c.qty, price=c.price,
                                  fee=self._fee(lot.symbol, c.exchange_order_id, per_order[c.exchange_order_id]),
                                  result_id=c.result_id, decision_id=None))
            dist = abs(lot.entry_price - self.stop_price_of(lot))
            flying = lot.in_flight
            rec_lot = Lot(lot_id=lot.lot_id, account_id=self.acct, symbol=lot.symbol, side=Side(lot.side),
                          source=LotSource.STRATEGY, slot_id=self.cfg.slot_id, timeframe=self.cfg.timeframe,
                          opened_at_ms=lot.opened_at_ms, qty=lot.qty, avg_price=lot.avg_price,
                          initial_qty=lot.initial_qty, max_qty=lot.max_qty, risk_distance=dist,
                          risk_usd=lot.initial_qty * dist, stop=prot, fills=tuple(fills),
                          in_flight=None if flying is None else flying.intent_id, adopted_by=None, tp1_done=False,
                          ladder_done=(), adds_done=len(lot.add_fills))
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
        """TradeOutcome of every closed lot, in entry order. Cowork h6: a lot whose fills / funding are not PROVEN
        (unreadable, empty, short, stale, mismatched) is PENDING - never booked with a zero fee - with an incident."""
        out = []
        for lot in self.fold.lots():
            if lot.open:
                continue
            try:
                out.append(trade_outcome(self.fold, lot, self.venue, self.reads, self.cfg.tf_ms,
                                         self.stop_price_of(lot), now=self.now))
            except LookupError as ex:
                if lot.lot_id not in self._pending_trades:
                    self._pending_trades.add(lot.lot_id)
                    self._incident(f'{lot.lot_id}: trade not booked - evidence not proven ({ex}); pending')
        return out

    def summary(self):
        eq = self.reads.equity()
        try:
            own = str(self.portfolio().ownership)
        except Exception:
            own = None
        return summarize(self.trades(), equity_end=eq.value[0] if eq.kind is ReadKind.OK else None,
                         open_lots=len(self.fold.open_lots()), ownership=own,
                         mode=str(self.fold.mode) if self.hard_hold is None else 'hold(durability_unavailable)',
                         counters=dataclasses.asdict(self.counters), stop_routes=self.stop_routes_text())

    def stop_routes(self):
        """H4: (symbol, side, route) of every open lot's carrying stop (classic | algo; 'none' when unprotected)."""
        out = []
        for lot in self.fold.open_lots():
            c = lot.carrier
            route = 'none' if c is None else (route_of(c.intent_id, c.intent.client_order_id) or 'classic')
            out.append((lot.symbol, lot.side, route))
        return tuple(sorted(out))

    def stop_routes_text(self):
        return ','.join(f'{s}:{sd}:{r}' for s, sd, r in self.stop_routes()) or '-'
