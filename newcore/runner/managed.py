"""M4: position management (NC-07 ManagementDriver) wired into the runner, behind `management.enabled` (default false).

`ManagedRunner` / `ManagedBookRunner` are the S1 Runner / S4 BookRunner plus `ManagementMixin`. With management
disabled (or for a lot that has no plan) every path is the unmanaged runner's, byte for byte.

One driver per managed lot. A lot is managed when management is enabled, the plan factory returns a plan for it at its
CONFIRMED entry fill, and no unmanaged (runner) child intent of it is in the journal. The driver is pure; the runner
owns identity, durability and the venue:

  journal -> driver   every driver input is a journal event, applied right after it is durable (`_emit`), and a
                      restart folds the SAME events through the SAME function (`_mg_observe`) at boot:
                        entry ResultObserved FINAL executed > 0     -> driver.start(plan)   (entry fee = venue fills)
                        ResultObserved of a driver intent           -> on_outcome (KNOWN / FINAL / refused = REJECTED);
                                                                       a FINAL with execution -> on_fills(venue fills)
                        the tick decision (WAIT, 'mg tick <open> <close_request>') -> on_candle(closed candle from the
                                                                       bars, strategy close request) then on_mark(close)
                        ModeChanged                                 -> set_mode (HOLD / PAUSED / ... table)
  driver -> journal   `_mg_flush`: every driver binding with no journaled intent is decided (child decision id,
                      reason = the core's), recorded DURABLE and sent with the runner's own send machinery (stop:
                      _send_stop + same-id re-send on NOT_FOUND; close / reduce: _send_close, HOLD on an unknown answer;
                      add: market, HOLD + query on an unknown answer); every binding the driver is cancelling is moved
                      to CANCELLING and cancelled (sync re-cancels one that never reached the venue). Held drafts stay
                      in the driver until set_mode releases them. Re-entrant-safe: flush runs only at cycle phase
                      boundaries, never inside `_emit`.
  driver reconcile    items (unmatched fill / unknown order) go to `management.reconciler` - the stub records an
                      incident and a durable HOLD; the REC-02 fold (newcore/reconcile, built separately) slots in there.

Cycle: sync (driver outcomes / fills) -> reconcile -> protect (unmanaged lots secure; managed lots flush) -> decide:
each managed lot's tick first (its strategy CLOSE becomes the tick's close request), then the unmanaged decide (CLOSE
of an unmanaged lot, ENTER) -> reconcile + invariants (I1 counts only CONFIRMED stops: the carrier, never an
unpromoted replacement).

Identity: every management intent is a lineage child of the lot (Grammar G5: derive_child_intent_id(account, lot,
purpose, journal ordinal)) and the driver counts the same ordinals (stamped when a draft is SENT), so the journal order
is the driver's order. The add is the one unkeyed opening decision (Runner._lineage_add; G4 forbids a keyed decision
from authorizing a lineage id).
Route (TNET-01): every draft carries its route and is sent on it (the client id is client_id_for(intent, route), so
`_ref` and the venue follow it). A REFUSED classic plan stop is never handed to the driver as a plain venue answer: the
refusal code is not journaled, so the runner first journals a route marker (WAIT 'mg fallback <intent>' when the venue
named the algo service - or the code is unknown after a restart: protection outranks, as Runner._next_route - else
'mg refused <intent>'), and the marker is the driver input: route_fallback() -> a NEW algo PROTECT child (G6), or
on_outcome(REJECTED). Lost answers resolved to a refusal take the same path, so fold / replay stay identical.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from newcore.domain import (Action, Authority, DecisionRecorded, EntriesMode, Evidence, IntentState, ModeChanged,
                            Purpose, ReasonCode, ResultObserved, ResultPhase, Rounding, Side)
from newcore.domain.errors import DomainError
from newcore.management import Candle, CostModel, ManagementError, PlanRefused, Stage, build_plan, range_bb_mr_v1
from newcore.management import driver as DR
from newcore.management.presets import CANDLE_SECONDS, STOP_ATR
from newcore.ports.keys import route_of
from newcore.ports.venue import MarketOrder, OrderOutcome, OutcomeKind, ReadKind

from . import ids
from .book import BookRunner
from .runner import ALGO_ROUTE, Runner
from .signals import CLOSE

ZERO = Decimal(0)
MG = 'mg '                                   # every management decision's detail starts with it
TICK = 'mg tick'
FALLBACK = 'mg fallback'                     # route marker: the refused classic stop goes to the algo route (G6)
REFUSED = 'mg refused'                       # route marker: the refusal goes to the driver (Rejected)
# driver reconcile tokens that stop new risk (incident + HOLD); every other token is an incident only, and the transient
# fill bookkeeping tokens are not reported at all (a persistent gap shows in the runner's reconciliation)
HOLD_ITEMS = frozenset({'unmatched_fill', 'unknown_order', 'close_found_nothing', 'fill_refused', 'core_refused',
                        'core_loop', 'route_fallback_refused', 'driver_refused', 'adopted_add'})
QUIET_ITEMS = frozenset({'fill_gap', 'deferred_fill'})
TICK_REASON = ReasonCode.PROTECT_CHECKING    # no management-tick code in the NC-01 registry (reported)
MAX_FLUSH = 32
MAX_REFUSALS = 2                             # refused management submits per lot per cycle, then HOLD (bounded)
ACTIONS = {Purpose.PROTECT: Action.PROTECT, Purpose.ADD: Action.ADD, Purpose.REDUCE: Action.REDUCE,
           Purpose.CLOSE: Action.CLOSE}
PROTECTIVE_EXITS = frozenset({ReasonCode.EXIT_STOP_CROSSED, ReasonCode.EXIT_STOP_FAILED, ReasonCode.EXIT_FLATTEN})


# ------------------------------------------------------------------------------------------------------- config
@dataclass(frozen=True)
class EntryInfo:
    """What a plan factory sees at the confirmed entry fill (all from the journal + the bars: restart-stable)."""
    lot_id: str
    symbol: str
    side: Side
    entry_price: Decimal
    entry_qty: Decimal
    entry_candle_open_ms: int                # the candle the market entry filled in (= the signal candle's close)
    signal_close_ms: int
    stop_distance: Decimal | None            # the entry signal's stop distance (re-derived from the bars)
    rules: object
    tf_ms: int


def hold_reconciler(runner, lot_id, items):
    """Stub for the REC-02 fold: a driver reconcile item that can mean unowned / unbooked exposure is an incident and a
    durable HOLD (entries stop, protect / close still pass); informational items are incidents only."""
    hold = False
    for item in items:
        token = item[0]
        if token in QUIET_ITEMS:
            continue
        runner._incident(f'management {lot_id}: ' + ' '.join(str(x) for x in item))
        hold = hold or token in HOLD_ITEMS
    if hold:
        runner._hold([ReasonCode.RECONCILE_UNRECONCILED])


@dataclass(frozen=True)
class ManagementConfig:
    enabled: bool = False                    # default OFF: management is opt-in per run
    plans: object = None                     # callable(EntryInfo) -> ManagementPlan | None (None: unmanaged lot)
    reconciler: object = hold_reconciler     # callable(runner, lot_id, items): the REC-02 slot
    quote_asset: str = 'USDT'                # fees in this asset are booked as they are
    fee_rates: tuple = ()                    # ((asset, Decimal rate in quote), ...) for fees in another asset


def _away(side):
    return Rounding.DOWN if side is Side.LONG else Rounding.UP


@dataclass(frozen=True)
class SyntheticPlans:
    """A mechanics plan in units of the entry's stop distance d (the R the runner sized on): stop at entry -/+ d, one
    DCA add at add_r x d (adverse side), TP1 / TP2 offsets in d from the basket average, net break-even after TP1, a
    time exit in candles. risk_cap = cap_mult x entry qty x d (the reserved add must fit; build_plan drops it if not)."""
    costs: CostModel
    add_r: Decimal | None = Decimal('0.5')
    add_scale: Decimal = Decimal('1')
    tp1_r: Decimal | None = Decimal('1')
    tp1_frac: Decimal | None = Decimal('0.5')
    tp2_r: Decimal | None = Decimal('2')
    be_after_tp1: bool = True
    time_exit_candles: int | None = 12
    trail_r: Decimal | None = None
    cap_mult: Decimal = Decimal('2.5')

    def __call__(self, info):
        d = info.stop_distance
        if d is None or d <= 0:
            return None
        s = 1 if info.side is Side.LONG else -1
        r = info.rules
        stop = r.quantize_price(info.entry_price - s * d, _away(info.side))
        add = None if self.add_r is None else r.quantize_price(info.entry_price - s * self.add_r * d, _away(info.side))
        return build_plan(rules=r, side=info.side, entry_price=info.entry_price, entry_qty=info.entry_qty,
                          entry_candle_open_ms=info.entry_candle_open_ms, candle_seconds=info.tf_ms // 1000,
                          stop_price=stop, risk_cap=self.cap_mult * info.entry_qty * d, costs=self.costs,
                          add_price=add, add_scale=None if add is None else self.add_scale,
                          tp1_frac=None if self.tp1_r is None else self.tp1_frac,
                          tp1_offset=None if self.tp1_r is None else self.tp1_r * d,
                          tp2_offset=None if self.tp2_r is None else self.tp2_r * d,
                          be_after_tp1=self.be_after_tp1 and self.tp1_r is not None,
                          time_exit_candles=self.time_exit_candles,
                          trail_offset=None if self.trail_r is None else self.trail_r * d).plan


@dataclass(frozen=True)
class RangeFixturePlans:
    """RANGE-BB-MR.v1 (newcore.management.presets) as the DISABLED mechanics fixture (4h only; no edge is claimed: the
    research verdict REJECTED it). ATR0 = the entry signal's stop distance / STOP_ATR (the runner sized the entry on a
    2-ATR stop, so the plan's basket stop is the stop the entry was sized for). risk_cap = cap_mult x the entry's risk
    to that stop (NC-06 sizing of the reserved add is not built: the cap is the fixture's)."""
    costs: CostModel
    cap_mult: Decimal = Decimal('2.5')

    def __call__(self, info):
        if info.tf_ms != CANDLE_SECONDS * 1000 or info.stop_distance is None or info.stop_distance <= 0:
            return None
        atr0 = info.stop_distance / STOP_ATR
        return range_bb_mr_v1(rules=info.rules, side=info.side, entry_price=info.entry_price,
                              entry_qty=info.entry_qty, entry_candle_open_ms=info.entry_candle_open_ms, atr0=atr0,
                              risk_cap=self.cap_mult * info.entry_qty * info.stop_distance, costs=self.costs).plan


# ------------------------------------------------------------------------------------------------------- the mixin
class ManagementMixin:
    def __init__(self, config, *, management=None, **kw):
        self.mgmt = management or ManagementConfig()
        self.mg = {}                         # lot id -> DriverState
        self.plans = {}                      # lot id -> ManagementPlan
        self.unmanaged = set()               # lots decided unmanaged (no plan / runner children)
        self._drafts = {}                    # intent id -> Draft (every draft the drivers produced)
        self._cancel_reason = {}             # intent id -> reason of the driver's cancel
        self._items = []                     # (lot id, reconcile items) not handled yet
        self._mg_mode = (EntriesMode.ACTIVE, None)
        self._refused = {}                   # lot id -> (cycle ms, refused submits this cycle)
        super().__init__(config, **kw)
        if self.mgmt.enabled:                # restart: fold the journal through the driver (same function as live)
            for ev in self.journal.read():
                self._mg_observe(ev)
            self._items = []                 # handled by the process that saw them (the HOLD is durable)

    # ----------------------------------------------------------------------------------------------- journal -> driver
    def _emit(self, cls, *, reason, **fields):
        ev = super()._emit(cls, reason=reason, **fields)
        if self.mgmt.enabled:
            self._mg_observe(ev)
        return ev

    def _mg_observe(self, ev):
        if isinstance(ev, ModeChanged):
            self._mg_mode = (ev.to_mode, ev.to_hold)
            for lot_id, ds in list(self.mg.items()):
                self._mg_drive(lot_id, DR.set_mode, ds, ev.to_mode, ev.to_hold)
        elif isinstance(ev, DecisionRecorded):
            d = ev.decision
            if d.action is Action.WAIT and d.subject_id in self.mg:
                if d.detail.startswith(TICK + ' '):
                    self._mg_apply_tick(d)
                elif d.detail.startswith((FALLBACK + ' ', REFUSED + ' ')):
                    self._mg_apply_marker(d)
        elif isinstance(ev, ResultObserved):
            r = ev.result
            iv = self.fold.intents[r.intent_id]
            if iv.purpose is Purpose.ENTRY:
                if r.phase is ResultPhase.FINAL and r.executed_qty and r.executed_qty > 0:
                    self._mg_start(iv, r)
                return
            lot_id = iv.intent.owner_id
            ds = self.mg.get(lot_id)
            if ds is None or not any(b.intent_id == iv.intent_id for b in ds.bindings):
                return
            if self._mg_classic_stop_refused(iv, r):
                return                                                    # the route marker carries it (see doc)
            conv = self._mg_outcome(iv, r)
            if conv is None:
                return
            out, submit = conv
            self._mg_drive(lot_id, DR.on_outcome, ds, out, submit=submit)
            if out.kind is OutcomeKind.FINAL and out.executed_qty > 0:
                self._mg_fills(lot_id, iv.intent.symbol, out.exchange_order_id)

    @staticmethod
    def _mg_classic_stop_refused(iv, r):
        return (iv.purpose is Purpose.PROTECT and r.phase is ResultPhase.FINAL and r.evidence is Evidence.EXCHANGE_REFUSED
                and route_of(iv.intent_id, iv.intent.client_order_id) == 'classic')

    def _mg_apply_marker(self, d):
        lot_id = d.subject_id
        kind, iid = d.detail[len(MG):].split(' ')[:2]
        ds = self.mg[lot_id]
        if kind == 'fallback':
            self._mg_drive(lot_id, DR.route_fallback, ds, iid)
            return
        iv = self.fold.intents[iid]
        out = OrderOutcome(kind=OutcomeKind.REJECTED, ref=self._ref(iv), observed_at_ms=d.at_ms, error_code=0)
        self._mg_drive(lot_id, DR.on_outcome, ds, out, submit=True)

    def _mg_outcome(self, iv, r):
        """The journaled OrderResult as the venue outcome the driver maps (None: nothing for the driver)."""
        ref = self._ref(iv)
        if r.phase is ResultPhase.KNOWN:
            return OrderOutcome(kind=OutcomeKind.KNOWN, ref=ref, observed_at_ms=r.observed_at_ms,
                                status=str(r.exchange_status), exchange_order_id=r.exchange_order_id), False
        if r.phase is not ResultPhase.FINAL:
            return None                                                   # UNKNOWN: the runner's sync resolves it
        if r.evidence is Evidence.POSITION_ADOPTED:                       # a lost add found in the position:
            self._items.append((iv.intent.owner_id, (('adopted_add', iv.intent_id),)))    # no fills to book: HOLD
            return None
        if r.evidence in (Evidence.EXCHANGE_REFUSED, Evidence.NOT_SENT, Evidence.NOT_FOUND_CORROBORATED):
            return OrderOutcome(kind=OutcomeKind.REJECTED, ref=ref, observed_at_ms=r.observed_at_ms,
                                error_code=0), True                       # the code is not journaled
        if r.exchange_order_id is None or r.exchange_status is None:
            return None
        return OrderOutcome(kind=OutcomeKind.FINAL, ref=ref, observed_at_ms=r.observed_at_ms,
                            status=str(r.exchange_status), exchange_order_id=r.exchange_order_id,
                            executed_qty=r.executed_qty, avg_price=r.avg_price if r.executed_qty > 0 else None), False

    def _mg_fills(self, lot_id, symbol, eoid):
        r = self.venue.fills(symbol, eoid)
        if r.kind is ReadKind.OK and r.value:
            self._mg_drive(lot_id, DR.on_fills, self.mg[lot_id], tuple(r.value))
            return True
        return False                                                      # re-read at the next flush

    def _mg_drive(self, lid, fn, /, *args, **kw):
        """One driver call. A driver refusal (ManagementError / an invalid record: e.g. a late answer for a position
        the driver already closed) changes nothing and becomes a reconcile item (incident + HOLD): never a crash of
        the cycle. Deterministic, so a restart folds the same refusal at the same event."""
        try:
            drive = fn(*args, **kw)
        except (ManagementError, DomainError) as ex:
            if lid in self.mg:
                self._items.append((lid, (('driver_refused', f'{fn.__name__}: {type(ex).__name__}: {ex}'[:200]),)))
            else:
                self._incident(f'management {lid}: driver refused {fn.__name__}: {ex}')
                self.unmanaged.add(lid)
            return
        self.mg[lid] = drive.state
        for d in drive.submits:
            self._drafts[d.intent_id] = d
        for c in drive.cancels:
            self._cancel_reason[c.intent_id] = c.reason
        if drive.reconcile:
            self._items.append((lid, drive.reconcile))

    def _mg_eligible(self, lot_id):
        """No unmanaged (runner) child of the lot in the journal (a lot opened before management was enabled stays
        the runner's)."""
        for iv in self.fold.intents.values():
            if iv.intent.owner_id == lot_id:
                d = self.fold.decisions.get(iv.intent.decision_id)
                if d is None or not d.detail.startswith(MG):
                    return False
        return True

    def _mg_start(self, entry_iv, r):
        lot_id = ids.derive_lot_id(self.acct, entry_iv.intent_id)
        if lot_id in self.mg or lot_id in self.unmanaged:
            return
        it = entry_iv.intent
        plan = None
        if self.mgmt.plans is not None and self._mg_eligible(lot_id):
            key = self.fold.entry_key(entry_iv)
            info = EntryInfo(lot_id=lot_id, symbol=it.symbol, side=Side(it.side), entry_price=r.avg_price,
                             entry_qty=r.executed_qty, entry_candle_open_ms=key.candle_close_ms,
                             signal_close_ms=key.candle_close_ms, stop_distance=self._entry_distance(entry_iv),
                             rules=self.cfg.rules[it.symbol], tf_ms=self.cfg.tf_ms)
            try:
                plan = self.mgmt.plans(info)
            except (PlanRefused, ManagementError, DomainError) as ex:
                self._incident(f'management {lot_id}: no plan ({type(ex).__name__}: {ex}); the runner protects it')
        if plan is None:
            self.unmanaged.add(lot_id)
            return
        fee = ZERO
        fr = self.venue.fills(it.symbol, r.exchange_order_id) if r.exchange_order_id is not None else None
        if fr is not None and fr.kind is ReadKind.OK:
            fee = sum((max(f.fee, ZERO) for f in fr.value), ZERO)
        else:
            self._incident(f'management {lot_id}: entry fills unreadable; the plan books a zero entry fee')
        self.plans[lot_id] = plan
        mode, hold = self._mg_mode
        self._mg_drive(lot_id, DR.start, plan, account_id=self.acct, lot_id=lot_id, entry_fee=fee, mode=mode,
                       hold_kind=hold, quote_asset=self.mgmt.quote_asset, fee_rates=tuple(self.mgmt.fee_rates))

    def _mg_candle(self, symbol, open_ms):
        r = self.bars.closed_bars(symbol, self.cfg.tf_ms, as_of_ms=open_ms + self.cfg.tf_ms, limit=1)
        if r.kind is not ReadKind.OK or not r.value or r.value[-1].open_ms != open_ms:
            raise LookupError(f'management tick: candle {symbol} {open_ms} not readable')
        b = r.value[-1]
        return Candle(open_ms=b.open_ms, open=b.open, high=b.high, low=b.low, close=b.close)

    def _mg_apply_tick(self, d):
        lot_id = d.subject_id
        _, _, open_ms, request = d.detail.split(' ')
        candle = self._mg_candle(d.symbol, int(open_ms))
        ds = self.mg[lot_id]
        if ds.pos.stage is not Stage.DONE:
            self._mg_drive(lot_id, DR.on_candle, ds, candle,
                           close_request=None if request == '-' else ReasonCode(request))
        ds = self.mg[lot_id]
        if ds.pos.stage is not Stage.DONE:
            self._mg_drive(lot_id, DR.on_mark, ds, candle.close)

    # ----------------------------------------------------------------------------------------------- driver -> venue
    def _mg_active(self, lot_id):
        ds = self.mg[lot_id]
        return ds.pos.stage is not Stage.DONE or bool(ds.bindings)

    def _mg_flush_all(self):
        for lot_id in list(self.mg):
            if self._mg_active(lot_id):
                self._mg_flush(lot_id)

    def _mg_flush(self, lot_id):
        for _ in range(MAX_FLUSH):
            if not self._mg_step(lot_id):
                break
        else:
            self._incident(f'management {lot_id}: flush did not settle in {MAX_FLUSH} rounds')
        items, self._items = self._items, []
        for lid, its in items:
            self.mgmt.reconciler(self, lid, its)

    def _mg_step(self, lot_id):
        """One unit of driver work (True = something was done; the driver state changed, so look again)."""
        ds = self.mg[lot_id]
        for b in ds.bindings:                                             # a FINAL whose fills were unreadable
            if b.state is DR.BindState.FINAL and b.executed and b.filled < b.executed and b.exchange_order_id:
                before = self.mg[lot_id]
                if self._mg_fills(lot_id, self.plans[lot_id].symbol, b.exchange_order_id) and \
                        self.mg[lot_id] != before:
                    return True
        for b in ds.bindings:                                             # a refused classic stop: route marker
            iv = self.fold.intents.get(b.intent_id)
            if (b.leg.value == 'stop' and b.route == 'classic' and b.state is DR.BindState.SENT and iv is not None
                    and iv.final is not None and iv.final.evidence is Evidence.EXCHANGE_REFUSED):
                self._mg_marker(lot_id, iv)
                return True
        for b in ds.bindings:                                             # drafts not journaled yet, in order
            if b.intent_id in self.fold.intents:
                continue
            d = self._drafts.get(b.intent_id)
            if d is None:
                self._incident(f'management {lot_id}: binding {b.intent_id} has no draft')
                continue
            if not self._permits(d.purpose, d.op):
                continue                                                  # held at the runner until permitted
            at, n = self._refused.get(lot_id, (None, 0))
            if at == self.now and n >= MAX_REFUSALS:
                return False                                              # bounded: retried next cycle (in HOLD)
            iv = self._mg_send_draft(lot_id, d)
            if iv.final is not None and iv.final.evidence is Evidence.EXCHANGE_REFUSED:
                n = (n if at == self.now else 0) + 1
                self._refused[lot_id] = (self.now, n)
                if n >= MAX_REFUSALS:
                    self._incident(f'management {lot_id}: {n} submits refused this cycle; HOLD')
                    self._hold([ReasonCode.EXEC_ORDER_FAILED], reason=ReasonCode.EXEC_ORDER_FAILED)
            return True
        for b in ds.bindings:                                             # cancels the driver asked for
            if b.state is DR.BindState.CANCELLING:
                iv = self.fold.intents.get(b.intent_id)
                if iv is not None and iv.live and iv.state is not IntentState.CANCELLING:
                    self._mg_cancel(iv, self._cancel_reason.get(b.intent_id, ReasonCode.PROTECT_REPLACE))
                    return True
        return False

    def _mg_marker(self, lot_id, iv):
        """Journal the route marker of a refused classic plan stop BEFORE the driver sees the refusal (the refusal code
        lives only in this process: after a restart it is unknown and protection outranks -> the algo route)."""
        code = self._refusals.get(iv.intent_id, 'unknown')
        kind = FALLBACK if code in ('unknown', ALGO_ROUTE) else REFUSED
        did = ids.marker_decision_id(kind, iv.intent_id)
        if self.journal.find_decision(did) is not None:
            return
        plan = self.plans[lot_id]
        self._decision(decision_id=did, action=Action.WAIT,
                       reason=ReasonCode.PROTECT_RESTORING if kind == FALLBACK else ReasonCode.EXEC_STOP_FAILED,
                       authority=Authority.PROTECTION, key=None, symbol=plan.symbol, side=str(plan.side),
                       subject_id=lot_id, evidence=(iv.intent_id,), detail=f'{kind} {iv.intent_id} {code}')

    def _mg_send_draft(self, lot_id, d):
        did = ids.child_decision_id(d.intent_id)
        prior = self.journal.find_decision(did)
        if prior is not None:                                             # crash gap: decided, intent not recorded
            planned = prior.decision.intents[0]
        else:
            planned = DR.to_order_intent(d, decision_id=did, at_ms=self.now)
            authority = Authority.PROTECTION if (d.purpose is Purpose.PROTECT or d.reason in PROTECTIVE_EXITS) \
                else Authority.STRATEGY
            px = f' @ {d.stop_price}' if d.stop_price is not None else ''
            self._decision(decision_id=did, action=ACTIONS[d.purpose], reason=d.reason, authority=authority, key=None,
                           symbol=d.symbol, side=str(d.side), intents=(planned,), subject_id=lot_id,
                           detail=f'{MG}{d.leg} {d.qty}{px}')
        iv = self._record_durable(planned)
        self._mg_send(iv)
        return iv

    def _mg_send(self, iv):
        p = iv.purpose
        if p is Purpose.PROTECT:
            self._send_stop(iv)
        elif p is Purpose.ADD:
            self._state(iv, IntentState.SUBMITTED)
            it = iv.intent
            out = self.venue.submit_market(MarketOrder(ref=self._ref(iv), position_side=str(it.side), qty=it.qty,
                                                       reduce=False))
            self._apply(iv, out, submit=True)
            if iv.live:                                                   # lost / acknowledged: HOLD, resolve
                self._hold([ReasonCode.EXEC_ENTRY_UNCONFIRMED], reason=ReasonCode.EXEC_ENTRY_UNCONFIRMED)
                self._resolve(iv)
        else:
            self._send_close(iv)

    def _mg_cancel(self, iv, reason):
        self._state(iv, IntentState.CANCELLING, reason)
        self._apply(iv, self.venue.cancel(self._ref(iv)), submit=False)
        if iv.live:
            self._apply(iv, self.venue.query(self._ref(iv)), submit=False)

    def _mg_managed(self, symbol, side):
        return [x.lot_id for x in self.fold.open_lots()
                if x.symbol == symbol and x.side == side and x.lot_id in self.mg]

    # ----------------------------------------------------------------------------------------------- runner hooks
    def _protect_all(self):
        for lot in self.fold.open_lots():
            if lot.lot_id in self.mg:
                continue
            d = self.fold.pending_closes.get(lot.lot_id)
            if d is not None and lot.closing is None:
                self._close_lot(lot, reason=d.reason, key=d.key)
            self._secure(lot.lot_id)
        self._mg_flush_all()
        for lot in self.fold.open_lots():
            if lot.lot_id in self.mg and lot.live_stop is None and lot.in_flight is None:
                self._mg_detach(lot)

    def _mg_detach(self, lot):
        """Safety net (Cowork M4 HIGHs): an open managed lot with NO live stop and nothing closing it after the
        driver ran (its stop was cancelled / expired outside the bot, a partial stop fill left a remainder, the driver
        refused an input) is handed back to the runner, which secures it at once (stop at the plan's latest level,
        else a reduce-only close, else HOLD). The hand-back is durable: the runner's own PROTECT child of the lot is
        what a restart reads (_mg_eligible), so the lot boots unmanaged."""
        self._incident(f'management {lot.lot_id}: no live stop after the driver ran; the runner protects the lot')
        self.mg.pop(lot.lot_id)
        self.unmanaged.add(lot.lot_id)
        self._hold([ReasonCode.PROTECT_RESTORING], reason=ReasonCode.PROTECT_RESTORING)
        self._secure(lot.lot_id)

    def _protect_price(self, lot):
        mg = [p for p in lot.protects if self.fold.decisions[p.intent.decision_id].detail.startswith(MG)]
        if mg:
            return mg[-1].intent.stop_price          # handed back: the plan's latest level (break-even / trail)
        return super()._protect_price(lot)

    def _secure(self, lot_id):
        if lot_id in self.mg:
            self._mg_flush(lot_id)
        else:
            super()._secure(lot_id)

    def _send_durable(self, iv):
        if iv.intent.owner_id in self.mg:
            self._mg_send(iv)                                             # a driver intent a crash left unsent
        else:
            super()._send_durable(iv)

    def _decide_all(self):
        if self.mg:
            for sym in self.cfg.symbols:
                bars = self._bars_now(sym)
                if bars is None:
                    continue
                closes = {}
                for s in self.signals.decide(sym, bars, self.now):
                    if s.action == CLOSE and s.side in self.cfg.sides:
                        closes.setdefault(s.side, s.reason)
                for side in ('LONG', 'SHORT'):
                    for lot_id in self._mg_managed(sym, side):
                        self._mg_tick(lot_id, bars[-1], closes.get(side))
        super()._decide_all()

    def _mg_tick(self, lot_id, bar, close_reason):
        ds, plan = self.mg[lot_id], self.plans[lot_id]
        if ds.pos.stage is Stage.DONE or bar.open_ms < plan.entry_candle_open_ms:
            return
        last = ds.pos.last_candle_open_ms
        if last is not None and bar.open_ms <= last:
            return
        did = ids.tick_decision_id(lot_id, bar.open_ms)
        if self.journal.find_decision(did) is None:
            self._decision(decision_id=did, action=Action.WAIT, reason=TICK_REASON, authority=Authority.STRATEGY,
                           key=None, symbol=plan.symbol, side=str(plan.side), subject_id=lot_id,
                           detail=f'{TICK} {bar.open_ms} {close_reason or "-"}')
        self._mg_flush(lot_id)

    def _exit(self, symbol, s):
        if self._mg_managed(symbol, s.side):
            return                                                        # the tick carried it as a close request
        super()._exit(symbol, s)


class ManagedRunner(ManagementMixin, Runner):
    """The S1 Runner with M4 management (identical to Runner while management is disabled)."""


class ManagedBookRunner(ManagementMixin, BookRunner):
    """The S4 BookRunner with M4 management (identical to BookRunner while management is disabled)."""
