"""ManagementDriver: the pure mapping layer between the S1 runner and the management core (NC-07 / M4).

Pure and deterministic: no IO, no clock, no venue calls. Every entry point takes a frozen `DriverState` plus one runner
event and returns `Drive(state, submits, cancels, steps, reconcile)`; the runner journals and sends `submits` and
`cancels` (in that order) and feeds the venue's answers back. The same event log always folds to the same state, so a
restart replays the durable log (`fold`) and lands on the identical state.

Mapping, venue -> core (step() inputs):
- `on_fills(VenueFill...)`: deduped by the venue trade id, matched to a leg through the exchange order id learned from
  the order's KNOWN / FINAL outcome -> ConfirmedFill(fill_id = trade id). A fee rebate (negative fee) counts as 0 cost.
  An unmatched fill books nothing and is returned in `reconcile`.
- `on_outcome(OrderOutcome, submit=...)`: KNOWN confirms an order (a stop becomes WORKING); FINAL records the executed
  quantity, and the leg completes once its fills add up to it (a short market fill re-arms its trigger / retries the
  close); REJECTED on a submit -> Rejected(leg); a cancel confirmed by FINAL -> Cancelled(leg) for a leg the core
  cancelled; UNKNOWN / NOT_FOUND book nothing and are returned in `reconcile`; ACKNOWLEDGED changes nothing.
Mapping, core actions -> NC-01 OrderIntent drafts (`Draft`; `to_order_intent` stamps the PLANNED intent):
- PLACE / REPLACE_STOP -> PROTECT STOP_MARKET (a replacement is a NEW intent; the old stop is cancelled only after the
  new one is CONFIRMED); CANCEL_STOP -> a cancel of the stop orders;
- PLACE / REPLACE_TARGET and PLACE_ADD -> bot-side TRIGGERS (NC-01 has no resting reduce-only target): `on_mark(price)`
  fires a crossed trigger as a MARKET REDUCE (target) or MARKET ADD; the cancel of an armed trigger is confirmed at once;
- TIME_EXIT / CLOSE / REDUCE -> a MARKET CLOSE (the whole position) or REDUCE (a part).
Every intent is owned by the lot (owner_kind LOT) and keyed by `ports.keys.derive_child_intent_id(account, lot,
purpose, ordinal)`, the same derivation as Grammar.next_child_intent_id: the driver counts the ordinals per
(lot, purpose) from the `lineage` the runner seeds from its journal, so the runner journals the drafts in order.

Runner rules it enforces (Codex ruling 10):
- only CONFIRMED (KNOWN) stop coverage counts as protection (`confirmed_coverage`, `protected`);
- the ADD trigger is HELD (never fires) until a confirmed stop covers the whole position;
- an old stop is kept until its replacement is confirmed;
- fills are deduped by venue trade id (the core dedupes again by fill id);
- modes: every draft goes through NC-01 `permitted(mode, hold_kind, purpose, op)`. Protective stop placement and
  risk-reducing closes (stop failed / crossed / flatten) pass in HOLD; ADD, targets, management closes (time exit,
  signal, take-profit = Op.MANAGE) and any cancel of protection are HELD and released by `set_mode` once allowed.
"""
from __future__ import annotations

import dataclasses
import enum
from decimal import Decimal

from ..domain.base import CTX, ZERO, req
from ..domain.errors import DomainError
from ..domain.modes import EntriesMode, Op, Permission, permitted
from ..domain.orders import IntentState, OrderIntent, OrderType, OwnerKind, Purpose, Side
from ..domain.reasons import ReasonCode
from ..ports.keys import client_id_for, derive_child_intent_id
from ..ports.venue import OrderOutcome, OutcomeKind, VenueFill
from .actions import ActionKind
from .core import initial_state, step
from .plan import DIV, ManagementError, ManagementPlan
from .state import Cancelled, Candle, ConfirmedFill, Leg, Rejected, Stage

K = ActionKind
R = ReasonCode
MANAGE_REASONS = frozenset({R.EXIT_TIME, R.EXIT_SIGNAL, R.EXIT_TAKE_PROFIT, R.EXIT_TP1, R.EXIT_BASKET_TP, R.EXIT_LADDER,
                            R.EXIT_BASKET_TP_PART})
CANCELLED_STATUSES = frozenset({'CANCELED', 'EXPIRED', 'EXPIRED_IN_MATCH'})
MAX_LOOPS = 8


class BindState(enum.StrEnum):
    SENT = 'sent'                # submitted, not confirmed
    WORKING = 'working'          # the venue confirmed it open (KNOWN)
    CANCELLING = 'cancelling'    # a cancel was sent
    FINAL = 'final'              # the venue answered FINAL; waiting for its fills to add up


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Draft:
    """One order the runner makes durable and sends (an NC-01 OrderIntent once `to_order_intent` stamps it)."""
    intent_id: str | None         # assigned when the draft is SENT (a held draft consumes no lineage ordinal)
    client_id: str | None
    route: str
    account_id: str
    symbol: str
    leg: Leg
    purpose: Purpose
    owner_kind: OwnerKind
    owner_id: str
    order_type: OrderType
    side: Side                   # POSITION side (a close of a LONG is LONG)
    qty: Decimal
    stop_price: Decimal | None
    reduce_only: bool
    reason: ReasonCode
    op: Op


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CancelDraft:
    intent_id: str
    client_id: str
    route: str
    leg: Leg
    purpose: Purpose
    reason: ReasonCode


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Binding:
    """A sent order of one leg (intent id / client id = the runner's journal identity)."""
    leg: Leg
    intent_id: str
    client_id: str
    purpose: Purpose
    qty: Decimal
    stop_price: Decimal | None
    trigger: Decimal | None      # the trigger level a fired market order came from (re-armed if it fills short)
    reason: ReasonCode
    state: BindState
    exchange_order_id: str | None
    filled: Decimal
    executed: Decimal | None     # FINAL executed quantity
    current: bool                # the core's current request for the leg (False: superseded by a replacement)
    core_cancelled: bool         # the core cancelled it: its Cancelled(leg) goes back to the core
    replaces: str | None         # the stop intent this one replaces (cancelled once this one is confirmed)
    status: str | None           # the venue status of its FINAL answer
    route: str                   # classic | algo (the venue endpoint its client id was derived for)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class PendingFill:
    """A venue fill matched to the order (intent) it executed, waiting to be booked by the core."""
    fill: ConfirmedFill
    intent_id: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Trigger:
    leg: Leg                     # TP1 / TP2 (REDUCE) or ADD
    price: Decimal
    qty: Decimal
    reason: ReasonCode


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DriverState:
    plan: ManagementPlan
    account_id: str
    lot_id: str
    route: str
    pos: object                  # PositionState
    bindings: tuple              # Binding
    triggers: tuple              # Trigger
    held: tuple                  # Draft / CancelDraft withheld by the mode table, in order
    lineage: tuple               # ((owner_id, purpose value, next ordinal), ...)
    trade_ids: tuple
    deferred: tuple              # PendingFill the core cannot book yet (e.g. a stop fill before the add it closes)
    mode: EntriesMode
    hold_kind: object            # HoldKind | None
    waiting: tuple               # market CLOSE / REDUCE drafts waiting for in-flight reduce orders to settle
    quote_asset: str             # fees in this asset are booked as they are
    fee_rates: tuple             # ((asset, rate in quote), ...) for fees charged in another asset
    stop_route: str              # the route new stops go out on (algo once the venue refused a classic stop)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Drive:
    state: DriverState
    submits: tuple               # Draft, in journal / send order
    cancels: tuple               # CancelDraft
    steps: tuple                 # the core Steps taken (audit)
    reconcile: tuple             # (token, detail) to reconcile; nothing was booked for them


# ------------------------------------------------------------------------------------------------------- queries
def confirmed_coverage(ds):
    """Quantity CONFIRMED protection covers: the largest WORKING stop, plus what a stop the venue reported FINAL has
    executed and is not booked yet (that part of the position is already closed at the venue). A requested / sent stop
    counts for nothing."""
    q = [b.qty for b in ds.bindings if b.leg is Leg.STOP and b.state is BindState.WORKING]
    out = max(q) if q else ZERO
    for b in ds.bindings:
        if b.leg is Leg.STOP and b.state is BindState.FINAL and b.executed is not None:
            out = CTX.add(out, max(CTX.subtract(b.executed, b.filled), ZERO))
    return out


def protected(ds):
    return ds.pos.qty == 0 or confirmed_coverage(ds) >= ds.pos.qty


def armed_triggers(ds):
    """Triggers `on_mark` may fire now: the add only behind a confirmed stop; nothing the mode table forbids."""
    return tuple(t for t in ds.triggers if (t.leg is not Leg.ADD or protected(ds)) and _allowed(ds, *_purpose_op(t)))


def to_order_intent(d, *, decision_id, at_ms):
    """The NC-01 OrderIntent (PLANNED) a draft stands for; the runner makes it durable before it sends anything."""
    return OrderIntent(intent_id=d.intent_id, account_id=d.account_id, decision_id=decision_id,
                       client_order_id=d.client_id, purpose=d.purpose, order_type=d.order_type,
                       state=IntentState.PLANNED, symbol=d.symbol, side=d.side, qty=d.qty, reason=d.reason,
                       created_at_ms=at_ms, owner_id=d.owner_id, owner_kind=d.owner_kind, slot_id=None, price=None,
                       stop_price=d.stop_price, arm=None, alt_client_order_id=None, seen_qty=None, authorized_by=None)


# ------------------------------------------------------------------------------------------------------- helpers
def _allowed(ds, purpose, op):
    return permitted(ds.mode, ds.hold_kind, purpose, op) is Permission.ALLOWED


def _purpose_op(t):
    if t.leg is Leg.ADD:
        return Purpose.ADD, Op.PLACE
    return Purpose.REDUCE, Op.MANAGE


class _W:
    """Mutable working copy of one driver call (never escapes the call)."""

    def __init__(self, ds):
        self.ds = ds
        self.d = {f.name: getattr(ds, f.name) for f in dataclasses.fields(ds)}
        self.submits, self.cancels, self.steps, self.reconcile, self.core_in = [], [], [], [], []
        self.lost_stop = False

    # identity -------------------------------------------------------------------------------------------------
    def next_id(self, purpose):
        lin = dict(((o, p), n) for o, p, n in self.d['lineage'])
        key = (self.d['lot_id'], purpose.value)
        n = lin.get(key, 0)
        lin[key] = n + 1
        self.d['lineage'] = tuple(sorted((o, p, c) for (o, p), c in lin.items()))
        return derive_child_intent_id(self.d['account_id'], self.d['lot_id'], purpose, n)

    def draft(self, leg, purpose, order_type, qty, reason, op, stop_price=None, route=None):
        """A draft WITHOUT identity: its intent id (lineage ordinal) is taken only when it is sent."""
        return Draft(intent_id=None, client_id=None, route=route or self.d['route'], account_id=self.d['account_id'],
                     symbol=self.d['plan'].symbol, leg=leg, purpose=purpose, owner_kind=OwnerKind.LOT,
                     owner_id=self.d['lot_id'], order_type=order_type, side=self.d['plan'].side, qty=qty,
                     stop_price=stop_price, reduce_only=purpose is not Purpose.ADD, reason=reason, op=op)

    def stamp(self, d):
        iid = self.next_id(d.purpose)
        return dataclasses.replace(d, intent_id=iid, client_id=client_id_for(iid, d.route))

    # bindings -------------------------------------------------------------------------------------------------
    def bindings(self):
        return list(self.d['bindings'])

    def put(self, bs):
        self.d['bindings'] = tuple(bs)

    def replace_binding(self, old, **kw):
        self.put([dataclasses.replace(b, **kw) if b is old else b for b in self.bindings()])

    def drop_binding(self, old):
        self.put([b for b in self.bindings() if b is not old])

    def bind(self, d, trigger=None):
        b = Binding(leg=d.leg, intent_id=d.intent_id, client_id=d.client_id, purpose=d.purpose, qty=d.qty,
                    stop_price=d.stop_price, trigger=trigger, reason=d.reason, state=BindState.SENT,
                    exchange_order_id=None, filled=ZERO, executed=None, current=True, core_cancelled=False,
                    replaces=None, status=None, route=d.route)
        self.put([*self.bindings(), b])

    # sending through the mode table ---------------------------------------------------------------------------
    def send(self, item):
        """Send now, or hold it (mode table) / let it wait (an overlapping reduce). Returns the sent draft or None."""
        pos = self.d['pos']
        if isinstance(item, CancelDraft):
            flat_stop = item.purpose is Purpose.PROTECT and pos.qty == 0      # nothing left to protect (Cowork 7)
            if not flat_stop and not _allowed(_view(self), item.purpose, Op.CANCEL):
                self.d['held'] = (*self.d['held'], item)
                return None
            self.cancels.append(item)
            for b in self.bindings():
                if b.intent_id == item.intent_id:
                    self.replace_binding(b, state=BindState.CANCELLING)
            return None
        if not _allowed(_view(self), item.purpose, item.op):
            self.d['held'] = (*self.d['held'], item)
            return None
        if item.order_type is OrderType.MARKET and item.reduce_only and _reducing_in_flight(self):
            self.d['waiting'] = (*self.d['waiting'], item)          # never two reduces racing for one position
            return None
        d = self.stamp(item)
        self.submits.append(d)
        self.bind(d)
        return d


def _reducing_in_flight(w):
    """A market reduce order (target / close) that may still execute: sent, or FINAL with fills to come."""
    return any(b.leg in (Leg.TP1, Leg.TP2, Leg.CLOSE) and (b.state is BindState.SENT or
                                                          (b.state is BindState.FINAL and b.executed is not None
                                                           and b.filled < b.executed))
               for b in w.bindings())


def _view(w):
    return DriverState(**w.d)


def _check_lineage(lineage, lot_id):
    """The lineage the runner seeds from its journal: ((lot_id, purpose value, next ordinal), ...) - this lot only, a
    known purpose, a non-negative int ordinal, one entry per purpose (Cowork 10)."""
    seen = set()
    for i, e in enumerate(lineage):
        req(type(e) is tuple and len(e) == 3, f'lineage[{i}]', '(owner_id, purpose, next ordinal)')
        owner, purpose, n = e
        req(owner == lot_id, f'lineage[{i}].owner', 'another lot: the driver keys only its own lot')
        req(purpose in {p.value for p in Purpose}, f'lineage[{i}].purpose', f'unknown purpose {purpose!r}')
        req(type(n) is int and n >= 0, f'lineage[{i}].ordinal', 'a non-negative int')
        req(purpose not in seen, f'lineage[{i}]', 'one entry per purpose')
        seen.add(purpose)
    return tuple(sorted(lineage))


def _new_state(plan, account_id, lot_id, route, pos, lineage, mode, hold_kind, quote_asset, fee_rates):
    for i, e in enumerate(fee_rates):
        req(type(e) is tuple and len(e) == 2 and type(e[0]) is str and type(e[1]) is Decimal and e[1] > 0,
            f'fee_rates[{i}]', '(asset, positive Decimal rate in the quote asset)')
    return DriverState(plan=plan, account_id=account_id, lot_id=lot_id, route=route, pos=pos, bindings=(), triggers=(),
                       held=(), lineage=_check_lineage(tuple(lineage), lot_id), trade_ids=(), deferred=(),
                       mode=EntriesMode(mode), hold_kind=hold_kind, waiting=(), quote_asset=quote_asset,
                       fee_rates=tuple(sorted(fee_rates)), stop_route=route)


# --------------------------------------------------------------------------------------------------- core actions
def _apply_actions(w, actions):
    for a in actions:
        if a.kind in (K.PLACE_STOP, K.REPLACE_STOP):
            # (PROTECT, PLACE) is permitted in every mode, so a stop placement is never held. The old stop keeps
            # working (current=False) until the new one is CONFIRMED (_release_stop_replacements).
            same = [b for b in w.bindings() if b.leg is Leg.STOP and b.state in (BindState.SENT, BindState.WORKING)
                    and (b.stop_price, b.qty) == (a.price, a.qty)]
            for b in [x for x in w.bindings() if x.leg is Leg.STOP and x.current]:
                w.replace_binding(b, current=False)
            if same:                                 # that exact stop is already bound: never a duplicate (Cowork 11)
                b = next(x for x in w.bindings() if x.intent_id == same[-1].intent_id)
                w.replace_binding(b, current=True)
                continue
            old = [b for b in w.bindings() if b.leg is Leg.STOP and b.state in (BindState.SENT, BindState.WORKING)]
            d = w.send(w.draft(Leg.STOP, Purpose.PROTECT, OrderType.STOP_MARKET, a.qty, a.reason, Op.PLACE, a.price,
                               route=w.d['stop_route']))
            for b in w.bindings():
                if d is not None and b.intent_id == d.intent_id:
                    w.replace_binding(b, replaces=old[-1].intent_id if old else None)
        elif a.kind is K.CANCEL_STOP:
            for b in w.bindings():
                if b.leg is Leg.STOP and b.state in (BindState.SENT, BindState.WORKING):
                    w.replace_binding(b, core_cancelled=b.current)
                    w.send(CancelDraft(intent_id=b.intent_id, client_id=b.client_id, route=b.route,
                                       leg=Leg.STOP, purpose=Purpose.PROTECT, reason=a.reason))
        elif a.kind in (K.PLACE_TARGET, K.REPLACE_TARGET, K.PLACE_ADD):
            pass                                         # triggers mirror the core's requests (_sync_triggers)
        elif a.kind in (K.CANCEL_TARGET, K.CANCEL_ADD):
            for b in w.bindings():                       # a fired market order cannot be cancelled: its FINAL decides
                if b.leg is a.leg and b.current:
                    w.replace_binding(b, core_cancelled=True)
        elif a.kind in (K.TIME_EXIT, K.CLOSE, K.REDUCE):
            whole = a.qty >= w.d['pos'].qty
            purpose = Purpose.CLOSE if whole else Purpose.REDUCE
            op = Op.MANAGE if a.reason in MANAGE_REASONS else Op.PLACE
            w.send(w.draft(Leg.CLOSE, purpose, OrderType.MARKET, a.qty, a.reason, op))
        else:
            raise ManagementError('driver.action', f'unmapped action {a.kind}')


TRIGGER_REASON = {Leg.TP1: R.EXIT_TP1, Leg.TP2: R.EXIT_TAKE_PROFIT}


def _sync_triggers(w):
    """The bot-side triggers ARE the core's requested target / add orders, less what a fired market order of that leg
    still has in flight (so a level never fires twice for the same quantity)."""
    pos, plan, out = w.d['pos'], w.d['plan'], []
    for leg in (Leg.TP1, Leg.TP2, Leg.ADD):
        o = getattr(pos, leg.value)
        if o is None:
            continue
        flying = ZERO
        for b in w.bindings():
            if b.leg is leg and b.current:
                cap = b.qty if b.executed is None else b.executed
                flying = CTX.add(flying, CTX.subtract(cap, b.filled))
        q = CTX.subtract(o.qty, flying)
        if q > 0:
            reason = TRIGGER_REASON.get(leg) or (R.ENTRY_PYRAMID if plan.add_is_pyramid else R.ENTRY_DCA_LEVEL)
            out.append(Trigger(leg=leg, price=o.price, qty=q, reason=reason))
    w.d['triggers'] = tuple(out)


def _retire_racing(w):
    """Tell the core a cancelled / shrunk leg can no longer race (Cancelled(leg), a LATER step) once nothing at the
    venue can still execute for it: no fired market order of that target / add, and no stop order other than the
    core's current request."""
    pos = w.d['pos']
    for r in pos.racing:
        if r.leg is Leg.STOP:
            sources = [b for b in w.bindings() if b.leg is Leg.STOP and not (b.current and pos.stop is not None)]
        elif r.leg is Leg.CLOSE:                        # any market close order still unsettled may execute
            sources = [b for b in w.bindings() if b.leg is Leg.CLOSE]
        else:
            sources = [b for b in w.bindings() if b.leg is r.leg]
        if not sources and not any(pf.fill.leg is r.leg for pf in w.d['deferred']):   # a deferred fill needs it
            w.core_in.append(Cancelled(leg=r.leg))


def _step_into(w, batch, candle=None, close_request=None, funding=None):
    """One core step; True on success. The core is never handed an input it refuses: a venue-derived input the core
    cannot apply (a late / irrelevant refusal or confirmation) is reported for reconcile and skipped - one by one, so
    the rest of the batch still applies (Cowork 3: the fold of any journal is total)."""
    try:
        r = step(w.d['plan'], w.d['pos'], batch, candle, close_request=close_request, funding=funding)
    except DomainError as ex:
        if len(batch) > 1:
            ok = False
            for x in batch:
                ok = _step_into(w, (x,)) or ok
            if candle is not None or close_request is not None or funding is not None:
                ok = _step_into(w, (), candle, close_request, funding) or ok
            return ok
        w.reconcile.append(('core_refused', f'{type(ex).__name__}: {ex.path}'))
        return False
    w.steps.append(r)
    w.d['pos'] = r.state
    w.core_in = []
    _apply_actions(w, r.actions)
    _retire_racing(w)
    _sync_triggers(w)
    return True


def _run_core(w, confirmed=(), candle=None, close_request=None, funding=None):
    """step() with the inputs, then every queued racing retirement in a LATER step, until quiet."""
    batch = tuple(confirmed)
    for _ in range(MAX_LOOPS):
        if not _step_into(w, batch, candle, close_request, funding):
            return False
        if not w.core_in:
            return True
        batch, candle, close_request, funding = tuple(w.core_in), None, None, None
    w.reconcile.append(('core_loop', 'racing retirements did not settle'))
    return False


def _restore_stop(w, b):
    """A stop the core still requests is gone at the venue (cancelled / expired outside the bot, or executed only in
    part): re-protect what the core still holds at once with a NEW child intent (Cowork 1 / 2)."""
    pos = w.d['pos']
    if pos.qty == 0 or pos.stop is None or pos.stage is not Stage.ACTIVE:
        return
    live = [x for x in w.bindings() if x.leg is Leg.STOP and x.state in (BindState.SENT, BindState.WORKING)
            and x.current]
    if live:
        return
    w.reconcile.append(('stop_lost', b.intent_id, b.status))
    d = w.send(w.draft(Leg.STOP, Purpose.PROTECT, OrderType.STOP_MARKET, pos.stop.qty, R.PROTECT_RESTORING, Op.PLACE,
                       pos.stop.price, route=w.d['stop_route']))
    _ = d


def _settle(w):
    """Bindings whose FINAL executed quantity is fully booked are complete: retire them and tell the core."""
    for b in w.bindings():
        if b.state is not BindState.FINAL or b.executed is None or b.filled < b.executed:
            continue
        w.drop_binding(b)
        short = CTX.subtract(b.qty, b.executed)
        if b.leg is Leg.CLOSE and short > 0 and w.d['pos'].closing > 0:
            if b.executed > 0:
                w.core_in.append(Rejected(leg=Leg.CLOSE, qty=short))   # a partial close: re-request the part that died
            else:                                                  # a reduce-only close found NOTHING to reduce:
                w.reconcile.append(('close_found_nothing', b.intent_id))   # the venue is flatter than booked
        elif b.leg is Leg.STOP and short > 0 and b.current and not b.core_cancelled:
            _restore_stop(w, b)                                   # our stop died (partly) while still requested
        # a cancelled / shrunk / short-filled stop, target or add: _retire_racing clears its racing allowance


def _release_stop_replacements(w):
    """Once the current stop is CONFIRMED, cancel every older stop it supersedes (never before)."""
    cur = [b for b in w.bindings() if b.leg is Leg.STOP and b.current and b.state is BindState.WORKING]
    if not cur:
        return
    held = {h.intent_id for h in w.d['held'] if isinstance(h, CancelDraft)}
    for o in w.bindings():
        if (o.leg is Leg.STOP and not o.current and o.state in (BindState.SENT, BindState.WORKING)
                and o.intent_id not in held):
            w.send(CancelDraft(intent_id=o.intent_id, client_id=o.client_id, route=o.route, leg=Leg.STOP,
                               purpose=Purpose.PROTECT, reason=R.PROTECT_REPLACE))


def _release_waiting(w):
    """Send a waiting market close / reduce once no other reduce is in flight, sized to what the core still asks
    for (never more than the position): two reduces never race for one position (Cowork 6)."""
    if not w.d['waiting'] or _reducing_in_flight(w):
        return
    pos, waiting, w.d['waiting'] = w.d['pos'], w.d['waiting'], ()
    q = min(pos.closing, pos.qty)
    for d in waiting:
        if q <= 0:
            w.reconcile.append(('close_dropped', d.reason.value))
            continue
        take = min(d.qty, q)
        q = CTX.subtract(q, take)
        purpose = Purpose.CLOSE if take >= pos.qty else Purpose.REDUCE
        w.send(dataclasses.replace(d, qty=take, purpose=purpose))


def _finish(w):
    _release_stop_replacements(w)
    for _ in range(MAX_LOOPS):
        w.core_in = []
        _settle(w)
        _retire_racing(w)
        if not w.core_in:
            break
        _run_core(w, tuple(w.core_in))
    _release_waiting(w)
    _sync_triggers(w)
    for b in w.bindings():                                       # an execution the venue reported, not booked yet
        if b.state is BindState.FINAL and b.executed is not None and b.filled < b.executed:
            w.reconcile.append(('fill_gap', b.intent_id, CTX.subtract(b.executed, b.filled)))
    return Drive(state=_view(w), submits=tuple(w.submits), cancels=tuple(w.cancels), steps=tuple(w.steps),
                 reconcile=tuple(w.reconcile))


# ------------------------------------------------------------------------------------------------- entry points
def start(plan, *, account_id, lot_id, entry_fee, lineage=(), route='classic', mode=EntriesMode.ACTIVE,
          hold_kind=None, quote_asset='USDT', fee_rates=()):
    """The confirmed entry fill opened lot `lot_id`: place the bracket (stop first; the add stays held).
    `lineage` = the journal's next ordinals of THIS lot's child intents ((lot_id, purpose value, n), ...)."""
    req(isinstance(plan, ManagementPlan), 'start.plan', 'a ManagementPlan')
    w = _W(_new_state(plan, account_id, lot_id, route, initial_state(plan, entry_fee), lineage, mode, hold_kind,
                      quote_asset, tuple(fee_rates)))
    _run_core(w)
    return _finish(w)


def _fee_in_quote(ds, f, w):
    """The fee in the quote asset (Cowork 5): as reported when it is the quote asset; converted at a supplied rate;
    otherwise ESTIMATED conservatively at the plan's taker rate on the notional and reported - never booked 1:1."""
    fee = max(f.fee, ZERO)                                       # a rebate counts as no cost (conservative)
    if f.fee_asset == ds.quote_asset:
        return fee
    rate = dict(ds.fee_rates).get(f.fee_asset)
    if rate is not None:
        return DIV.multiply(fee, rate)
    w.reconcile.append(('fee_asset_estimated', f.trade_id, f.fee_asset))
    return CTX.multiply(ds.plan.costs.taker_fee, CTX.multiply(f.qty, f.price))


def on_fills(ds, fills):
    """Venue executions (userTrades rows) -> ConfirmedFill inputs. Duplicates by trade id are no-ops."""
    w = _W(ds)
    seen = set(ds.trade_ids)
    by_xid = {b.exchange_order_id: b for b in w.bindings() if b.exchange_order_id is not None}
    conf = []
    for f in fills:
        req(isinstance(f, VenueFill), 'on_fills', 'VenueFill rows')
        if f.trade_id in seen:
            continue
        b = by_xid.get(f.exchange_order_id)
        if b is None or f.symbol != ds.plan.symbol or f.position_side != ds.plan.side.value:
            w.reconcile.append(('unmatched_fill', f.trade_id))
            continue
        seen.add(f.trade_id)
        conf.append(PendingFill(fill=ConfirmedFill(fill_id=f.trade_id, leg=b.leg, qty=f.qty, price=f.price,
                                                   fee=_fee_in_quote(ds, f, w)), intent_id=b.intent_id))
    w.d['trade_ids'] = tuple(sorted(seen))
    if conf or w.d['deferred']:
        _apply_fills(w, conf)
    return _finish(w)


def _apply_fills(w, conf):
    """Apply venue fills in an order the core accepts: opening (ADD) fills first, then the reducing fills one by one in
    the order the venue reported them. A reducing fill the core refuses only because an earlier venue event is not
    booked yet (a stop / close fill beyond the booked position while an add's fill is still on its way) is DEFERRED in
    the state together with every reducing fill after it (a FIFO barrier: reductions are never booked out of venue
    order) and retried with the next fills - never dropped, never forced. Deferred fills are reported for reconcile."""
    queue = [*w.d['deferred'], *conf]
    order = [*(x for x in queue if x.fill.leg is Leg.ADD), *(x for x in queue if x.fill.leg is not Leg.ADD)]
    kept, blocked = [], False
    for k, pf in enumerate(order, 1):
        # every fill not applied yet counts as deferred while the others run (it still needs its leg's allowance)
        w.d['deferred'] = tuple([*kept, *order[k:]])
        opening = pf.fill.leg is Leg.ADD
        if blocked and not opening:
            kept.append(pf)
            continue
        try:
            r = step(w.d['plan'], w.d['pos'], (pf.fill,))
        except ManagementError:
            kept.append(pf)
            blocked = blocked or not opening
            continue
        except DomainError as ex:                  # malformed against the plan (e.g. off the step): never booked
            w.reconcile.append(('fill_refused', pf.fill.fill_id, f'{type(ex).__name__}: {ex.path}'))
            continue
        w.steps.append(r)
        w.d['pos'] = r.state
        w.core_in = []
        _apply_actions(w, r.actions)
        _retire_racing(w)
        _sync_triggers(w)
        if w.core_in:
            _run_core(w, tuple(w.core_in))
        for b in w.bindings():                 # a binding counts a fill only once the core has BOOKED it
            if b.intent_id == pf.intent_id:
                w.replace_binding(b, filled=CTX.add(b.filled, pf.fill.qty))
    w.d['deferred'] = tuple(kept)
    w.reconcile.extend(('deferred_fill', pf.fill.fill_id) for pf in kept)


def on_outcome(ds, outcome, *, submit):
    """A venue answer for one of our orders (submit / cancel / query). Never raises for a venue answer."""
    req(isinstance(outcome, OrderOutcome), 'on_outcome', 'an OrderOutcome')
    w = _W(ds)
    b = next((x for x in w.bindings() if x.client_id == outcome.ref.client_id), None)
    k = outcome.kind
    if b is None:
        w.reconcile.append(('unknown_order', outcome.ref.client_id))
        return _finish(w)
    if k is OutcomeKind.KNOWN:
        if b.state is BindState.SENT:
            w.replace_binding(b, state=BindState.WORKING, exchange_order_id=outcome.exchange_order_id)
        else:
            w.replace_binding(b, exchange_order_id=outcome.exchange_order_id)
    elif k is OutcomeKind.FINAL:
        w.replace_binding(b, state=BindState.FINAL, exchange_order_id=outcome.exchange_order_id,
                          executed=outcome.executed_qty, status=outcome.status)
        if outcome.status in CANCELLED_STATUSES and b.leg is Leg.STOP and b.current and not b.core_cancelled:
            w.reconcile.append(('stop_ended_outside', b.intent_id, outcome.status))   # incident: not our cancel
    elif k is OutcomeKind.REJECTED:
        if (submit and b.leg is Leg.STOP and b.route == 'classic' and b.current and not b.core_cancelled
                and _needs_algo(outcome)):
            _fallback_to_algo(w, b)                  # never reaches the core: the stop request stays alive
            return _finish(w)
        if submit:
            # only a refusal of what the core STILL requests goes back to it; a refused order the core already
            # cancelled / superseded (e.g. a stop replacement refused after the position closed) is simply retired
            w.drop_binding(b)
            pos = ds.pos
            if b.leg is Leg.STOP:
                if b.current and pos.stop is not None and not b.core_cancelled and pos.stage is Stage.ACTIVE:
                    prev = [o for o in w.bindings() if o.leg is Leg.STOP and o.intent_id == b.replaces
                            and o.state in (BindState.SENT, BindState.WORKING)]
                    for o in prev:                     # the venue keeps the previous stop: it is current again
                        w.replace_binding(o, current=True)
                    w.core_in.append(Rejected(leg=Leg.STOP))
                    if not prev:                       # ... unless it already executed / is gone: nothing protects
                        w.lost_stop = True             # the rest -> the core closes it (stop failed)
                else:
                    w.reconcile.append(('late_refusal', b.intent_id))
            elif b.leg is Leg.CLOSE:
                if pos.closing > 0:
                    w.core_in.append(Rejected(leg=Leg.CLOSE, qty=b.qty))
            elif not b.core_cancelled and getattr(pos, b.leg.value) is not None:
                w.core_in.append(Rejected(leg=b.leg))
            if w.core_in:
                _run_core(w, tuple(w.core_in), close_request=R.EXIT_STOP_FAILED if w.lost_stop else None)
    elif k in (OutcomeKind.UNKNOWN, OutcomeKind.NOT_FOUND):
        w.reconcile.append((k.value, outcome.ref.client_id))      # books nothing: a NOT_FOUND is never "never filled"
    return _finish(w)


ALGO_ROUTE_CODES = frozenset({-4120, -1116, -1102, -4136})     # the venue wants the algo (conditional) endpoint


def _needs_algo(outcome):
    return outcome.detail == 'algo_route' or outcome.error_code in ALGO_ROUTE_CODES


def _fallback_to_algo(w, b):
    """Journal rule G6: the classic stop closed REJECTED; the SAME protection (price, qty) goes out as a NEW child
    intent on the algo route. The core never sees a refusal: its stop request stays alive, and the old stop it
    replaces (if any) keeps working until this one is confirmed."""
    w.drop_binding(b)
    w.d['stop_route'] = 'algo'                     # this venue wants stops on the algo route: every later one too
    d = w.send(w.draft(Leg.STOP, Purpose.PROTECT, OrderType.STOP_MARKET, b.qty, b.reason, Op.PLACE, b.stop_price,
                       route='algo'))
    for x in w.bindings():
        if d is not None and x.intent_id == d.intent_id:
            w.replace_binding(x, current=b.current, core_cancelled=b.core_cancelled, replaces=b.replaces)
    w.reconcile.append(('route_fallback', b.intent_id, None if d is None else d.intent_id))


def route_fallback(ds, refused_intent_id):
    """The runner resolved a classic stop as refused (journal G6) - e.g. after a lost answer was resolved: place the
    same protection on the algo route as a new child intent. Deterministic; fold() replays it as
    ('route_fallback', intent_id). A stop that is not an unresolved classic PROTECT is refused (reported), so both
    routes are never live at once."""
    w = _W(ds)
    b = next((x for x in w.bindings() if x.intent_id == refused_intent_id), None)
    if b is None or b.leg is not Leg.STOP or b.route != 'classic' or b.state is not BindState.SENT:
        w.reconcile.append(('route_fallback_refused', refused_intent_id))
        return _finish(w)
    _fallback_to_algo(w, b)
    return _finish(w)


def on_mark(ds, price):
    """Fire the bot-side triggers the price has crossed (targets first, then the add), as market orders."""
    w = _W(ds)
    long = ds.plan.side is Side.LONG
    order = {Leg.TP1: 0, Leg.TP2: 1, Leg.ADD: 2}
    for t in sorted(armed_triggers(ds), key=lambda t: order[t.leg]):
        if t.leg is Leg.ADD:                   # a DCA add triggers on the adverse side, a pyramid add on the profit side
            up = ds.plan.add_is_pyramid == long
            purpose, op = Purpose.ADD, Op.PLACE
        else:
            up = long
            purpose, op = Purpose.REDUCE, Op.MANAGE
        crossed = price >= t.price if up else price <= t.price
        if not crossed:
            continue
        if purpose is Purpose.REDUCE and _reducing_in_flight(w):
            continue                           # a target never races another reduce for the same position
        w.d['triggers'] = tuple(x for x in w.d['triggers'] if x is not t)
        d = w.stamp(w.draft(t.leg, purpose, OrderType.MARKET, t.qty, t.reason, op))
        w.submits.append(d)
        w.bind(d, trigger=t.price)
    return _finish(w)


def on_candle(ds, candle, *, close_request=None, funding=None):
    """A CLOSED candle (time exit, trail, stop-crossed check), optionally with a strategy exit or a funding payment."""
    req(isinstance(candle, Candle), 'on_candle', 'a closed Candle')
    w = _W(ds)
    if ds.pos.stage is not Stage.DONE:
        _run_core(w, (), candle, close_request, funding)
    return _finish(w)


def set_mode(ds, mode, hold_kind=None):
    """The portfolio mode changed: release every held draft the NC-01 table now permits, in order - except what is
    stale by now (Cowork 4): a close / reduce when nothing is being closed any more (sized down to what still is), a
    cancel of an order that is no longer working."""
    w = _W(ds)
    w.d['mode'], w.d['hold_kind'] = EntriesMode(mode), hold_kind
    held, w.d['held'] = w.d['held'], ()
    pos = w.d['pos']
    left = min(pos.closing, pos.qty)
    for item in held:
        if isinstance(item, CancelDraft):
            live = [b for b in w.bindings() if b.intent_id == item.intent_id
                    and b.state in (BindState.SENT, BindState.WORKING)]
            if not live:
                w.reconcile.append(('held_dropped', item.intent_id))
                continue
        elif item.leg is Leg.CLOSE:
            if left <= 0:
                w.reconcile.append(('held_dropped', item.reason.value))
                continue
            take = min(item.qty, left)
            left = CTX.subtract(left, take)
            item = dataclasses.replace(item, qty=take, purpose=Purpose.CLOSE if take >= pos.qty else Purpose.REDUCE)
        w.send(item)
    return _finish(w)


# ------------------------------------------------------------------------------------------------------ helpers
def lot_vs_venue(ds, venue_qty):
    """Compare the lot's booked position with the venue's position read (VenuePosition.qty) for reconcile (Cowork 9):
    the difference, what is still deferred / unbooked, and whether they explain it."""
    deferred = ZERO
    for pf in ds.deferred:
        deferred = CTX.add(deferred, pf.fill.qty) if pf.fill.leg is Leg.ADD else CTX.subtract(deferred, pf.fill.qty)
    unbooked = ZERO
    for b in ds.bindings:
        if b.state is BindState.FINAL and b.executed is not None and b.filled < b.executed:
            gap = CTX.subtract(b.executed, b.filled)
            unbooked = CTX.add(unbooked, gap) if b.leg is Leg.ADD else CTX.subtract(unbooked, gap)
    diff = CTX.subtract(venue_qty, ds.pos.qty)
    return dict(booked=ds.pos.qty, venue=venue_qty, diff=diff, deferred=deferred, unbooked=unbooked,
                explained=diff == CTX.add(deferred, unbooked))


# ------------------------------------------------------------------------------------------------------ restart
def fold(plan, *, account_id, lot_id, entry_fee, events, lineage=(), route='classic', quote_asset='USDT',
         fee_rates=()):
    """Rebuild the driver from its durable event log: ('fills', rows) / ('outcome', outcome, submit) / ('mark', price)
    / ('candle', candle, close_request, funding) / ('mode', mode, hold_kind). Returns every Drive, in order. Total:
    every venue-derived event folds (an input the core would refuse is reported, never raised)."""
    out = [start(plan, account_id=account_id, lot_id=lot_id, entry_fee=entry_fee, lineage=lineage, route=route,
                 quote_asset=quote_asset, fee_rates=fee_rates)]
    for e in events:
        ds = out[-1].state
        kind = e[0]
        if kind == 'fills':
            out.append(on_fills(ds, e[1]))
        elif kind == 'outcome':
            out.append(on_outcome(ds, e[1], submit=e[2]))
        elif kind == 'mark':
            out.append(on_mark(ds, e[1]))
        elif kind == 'candle':
            out.append(on_candle(ds, e[1], close_request=e[2], funding=e[3]))
        elif kind == 'mode':
            out.append(set_mode(ds, e[1], e[2]))
        elif kind == 'route_fallback':
            out.append(route_fallback(ds, e[1]))
        else:
            raise ManagementError('fold.events', f'unknown event {kind!r}')
    return tuple(out)
