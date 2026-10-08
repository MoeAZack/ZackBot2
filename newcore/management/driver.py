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
from ..domain.modes import EntriesMode, Op, Permission, permitted
from ..domain.orders import IntentState, OrderIntent, OrderType, OwnerKind, Purpose, Side
from ..domain.reasons import ReasonCode
from ..ports.keys import client_id_for, derive_child_intent_id
from ..ports.venue import OrderOutcome, OutcomeKind, VenueFill
from .actions import ActionKind
from .core import initial_state, step
from .plan import ManagementError, ManagementPlan
from .state import Cancelled, Candle, ConfirmedFill, Leg, Rejected, Stage

K = ActionKind
R = ReasonCode
MANAGE_REASONS = frozenset({R.EXIT_TIME, R.EXIT_SIGNAL, R.EXIT_TAKE_PROFIT, R.EXIT_TP1})
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
    intent_id: str
    client_id: str
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
    mode: EntriesMode
    hold_kind: object            # HoldKind | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Drive:
    state: DriverState
    submits: tuple               # Draft, in journal / send order
    cancels: tuple               # CancelDraft
    steps: tuple                 # the core Steps taken (audit)
    reconcile: tuple             # (token, detail) to reconcile; nothing was booked for them


# ------------------------------------------------------------------------------------------------------- queries
def confirmed_coverage(ds):
    """Quantity a CONFIRMED (WORKING) stop covers. A requested / sent stop counts for nothing."""
    q = [b.qty for b in ds.bindings if b.leg is Leg.STOP and b.state is BindState.WORKING]
    return max(q) if q else ZERO


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

    # identity -------------------------------------------------------------------------------------------------
    def next_id(self, purpose):
        lin = dict(((o, p), n) for o, p, n in self.d['lineage'])
        key = (self.d['lot_id'], purpose.value)
        n = lin.get(key, 0)
        lin[key] = n + 1
        self.d['lineage'] = tuple(sorted((o, p, c) for (o, p), c in lin.items()))
        return derive_child_intent_id(self.d['account_id'], self.d['lot_id'], purpose, n)

    def draft(self, leg, purpose, order_type, qty, reason, op, stop_price=None):
        iid = self.next_id(purpose)
        return Draft(intent_id=iid, client_id=client_id_for(iid, self.d['route']), route=self.d['route'],
                     account_id=self.d['account_id'], symbol=self.d['plan'].symbol, leg=leg, purpose=purpose,
                     owner_kind=OwnerKind.LOT, owner_id=self.d['lot_id'], order_type=order_type,
                     side=self.d['plan'].side, qty=qty, stop_price=stop_price, reduce_only=purpose is not Purpose.ADD,
                     reason=reason, op=op)

    # bindings -------------------------------------------------------------------------------------------------
    def bindings(self):
        return list(self.d['bindings'])

    def put(self, bs):
        self.d['bindings'] = tuple(bs)

    def replace_binding(self, old, **kw):
        self.put([dataclasses.replace(b, **kw) if b is old else b for b in self.bindings()])

    def drop_binding(self, old):
        self.put([b for b in self.bindings() if b is not old])

    # sending through the mode table ---------------------------------------------------------------------------
    def send(self, item):
        purpose, op = (item.purpose, item.op) if isinstance(item, Draft) else (item.purpose, Op.CANCEL)
        if not _allowed(_view(self), purpose, op):
            self.d['held'] = self.d['held'] + (item,)
            return
        self._dispatch(item)

    def _dispatch(self, item):
        if isinstance(item, CancelDraft):
            self.cancels.append(item)
            for b in self.bindings():
                if b.intent_id == item.intent_id:
                    self.replace_binding(b, state=BindState.CANCELLING)
            return
        self.submits.append(item)
        b = Binding(leg=item.leg, intent_id=item.intent_id, client_id=item.client_id, purpose=item.purpose,
                    qty=item.qty, stop_price=item.stop_price, trigger=None,
                    reason=item.reason, state=BindState.SENT, exchange_order_id=None, filled=ZERO, executed=None,
                    current=True, core_cancelled=False, replaces=None)
        self.put(self.bindings() + [b])


def _view(w):
    return DriverState(**w.d)


def _new_state(plan, account_id, lot_id, route, pos, lineage, mode, hold_kind):
    return DriverState(plan=plan, account_id=account_id, lot_id=lot_id, route=route, pos=pos, bindings=(), triggers=(),
                       held=(), lineage=tuple(sorted(lineage)), trade_ids=(), mode=EntriesMode(mode),
                       hold_kind=hold_kind)


# --------------------------------------------------------------------------------------------------- core actions
def _apply_actions(w, actions):
    plan = w.d['plan']
    for a in actions:
        if a.kind in (K.PLACE_STOP, K.REPLACE_STOP):
            # (PROTECT, PLACE) is permitted in every mode, so a stop placement is never held. The old stop keeps
            # working (current=False) until the new one is CONFIRMED (_release_stop_replacements).
            old = [b for b in w.bindings() if b.leg is Leg.STOP and b.current]
            for b in old:
                w.replace_binding(b, current=False)
            d = w.draft(Leg.STOP, Purpose.PROTECT, OrderType.STOP_MARKET, a.qty, a.reason, Op.PLACE, a.price)
            w.send(d)
            for b in w.bindings():
                if b.intent_id == d.intent_id:
                    w.replace_binding(b, replaces=old[-1].intent_id if old else None)
        elif a.kind is K.CANCEL_STOP:
            for b in w.bindings():
                if b.leg is Leg.STOP and b.state in (BindState.SENT, BindState.WORKING):
                    w.replace_binding(b, core_cancelled=b.current)
                    w.send(CancelDraft(intent_id=b.intent_id, client_id=b.client_id, route=w.d['route'],
                                       leg=Leg.STOP, purpose=Purpose.PROTECT, reason=a.reason))
            w.d['held'] = tuple(h for h in w.d['held'] if not (isinstance(h, Draft) and h.leg is Leg.STOP))
        elif a.kind in (K.PLACE_TARGET, K.REPLACE_TARGET, K.PLACE_ADD):
            pass                                         # triggers mirror the core's requests (_sync_triggers)
        elif a.kind in (K.CANCEL_TARGET, K.CANCEL_ADD):
            flying = [b for b in w.bindings() if b.leg is a.leg and b.current]
            if not flying:
                w.core_in.append(Cancelled(leg=a.leg))   # never sent: the cancel is confirmed at once (a later step)
            for b in flying:                             # a fired market order cannot be cancelled: its FINAL decides
                w.replace_binding(b, core_cancelled=True)
        elif a.kind in (K.TIME_EXIT, K.CLOSE, K.REDUCE):
            whole = a.qty >= w.d['pos'].qty
            purpose = Purpose.CLOSE if whole else Purpose.REDUCE
            op = Op.MANAGE if a.reason in MANAGE_REASONS else Op.PLACE
            w.send(w.draft(Leg.CLOSE, purpose, OrderType.MARKET, a.qty, a.reason, op))
        else:
            raise ManagementError('driver.action', f'unmapped action {a.kind}')
    _ = plan


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


def _run_core(w, confirmed=(), candle=None, close_request=None, funding=None):
    """step() with the inputs, then every queued trigger-cancel confirmation in a LATER step, until quiet."""
    batch = tuple(confirmed)
    for _ in range(MAX_LOOPS):
        r = step(w.d['plan'], w.d['pos'], batch, candle, close_request=close_request, funding=funding)
        w.steps.append(r)
        w.d['pos'] = r.state
        w.core_in = []
        _apply_actions(w, r.actions)
        _sync_triggers(w)
        if not w.core_in:
            return
        batch, candle, close_request, funding = tuple(w.core_in), None, None, None
    raise ManagementError('driver.core', 'trigger cancellations did not settle')


def _settle(w):
    """Bindings whose FINAL executed quantity is fully filled are complete: retire them and tell the core."""
    plan = w.d['plan']
    for b in w.bindings():
        if b.state is not BindState.FINAL or b.executed is None or b.filled < b.executed:
            continue
        w.drop_binding(b)
        short = CTX.subtract(b.qty, b.executed)
        if b.leg is Leg.STOP:
            if b.core_cancelled and short > 0:
                w.core_in.append(Cancelled(leg=Leg.STOP))
        elif b.leg is Leg.CLOSE:
            if short > 0 and w.d['pos'].closing > 0:
                w.core_in.append(Rejected(leg=Leg.CLOSE))          # the core re-requests the rest
        elif short > 0:                                            # a fired target / add filled short
            if b.core_cancelled:
                w.core_in.append(Cancelled(leg=b.leg))
    _ = plan


def _release_stop_replacements(w):
    """Once the current stop is CONFIRMED, cancel every older stop it supersedes (never before)."""
    cur = [b for b in w.bindings() if b.leg is Leg.STOP and b.current and b.state is BindState.WORKING]
    if not cur:
        return
    held = {h.intent_id for h in w.d['held'] if isinstance(h, CancelDraft)}
    for o in w.bindings():
        if (o.leg is Leg.STOP and not o.current and o.state in (BindState.SENT, BindState.WORKING)
                and o.intent_id not in held):
            w.send(CancelDraft(intent_id=o.intent_id, client_id=o.client_id, route=w.d['route'], leg=Leg.STOP,
                               purpose=Purpose.PROTECT, reason=R.PROTECT_REPLACE))


def _finish(w):
    _release_stop_replacements(w)
    for _ in range(MAX_LOOPS):
        w.core_in = []
        _settle(w)
        if not w.core_in:
            break
        _run_core(w, tuple(w.core_in))
    _sync_triggers(w)
    return Drive(state=_view(w), submits=tuple(w.submits), cancels=tuple(w.cancels), steps=tuple(w.steps),
                 reconcile=tuple(w.reconcile))


# ------------------------------------------------------------------------------------------------- entry points
def start(plan, *, account_id, lot_id, entry_fee, lineage=(), route='classic', mode=EntriesMode.ACTIVE,
          hold_kind=None):
    """The confirmed entry fill opened lot `lot_id`: place the bracket (stop first; the add stays held)."""
    req(isinstance(plan, ManagementPlan), 'start.plan', 'a ManagementPlan')
    w = _W(_new_state(plan, account_id, lot_id, route, initial_state(plan, entry_fee), lineage, mode, hold_kind))
    _run_core(w)
    return _finish(w)


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
        conf.append(ConfirmedFill(fill_id=f.trade_id, leg=b.leg, qty=f.qty, price=f.price, fee=max(f.fee, ZERO)))
        cur = next(x for x in w.bindings() if x.intent_id == b.intent_id)
        w.replace_binding(cur, filled=CTX.add(cur.filled, f.qty))
        by_xid[f.exchange_order_id] = next(x for x in w.bindings() if x.intent_id == b.intent_id)
    w.d['trade_ids'] = tuple(sorted(seen))
    if conf:
        _run_core(w, tuple(conf))
    return _finish(w)


def on_outcome(ds, outcome, *, submit):
    """A venue answer for one of our orders (submit / cancel / query)."""
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
                          executed=outcome.executed_qty)
    elif k is OutcomeKind.REJECTED:
        if submit:
            w.drop_binding(b)
            if b.leg is Leg.STOP:
                if b.current:                              # the venue keeps the previous stop: it is current again
                    prev = [o for o in w.bindings() if o.leg is Leg.STOP and o.intent_id == b.replaces]
                    for o in prev:
                        w.replace_binding(o, current=True)
                    w.core_in.append(Rejected(leg=Leg.STOP))
            elif b.leg is Leg.CLOSE:
                if ds.pos.closing > 0:
                    w.core_in.append(Rejected(leg=Leg.CLOSE))
            elif b.core_cancelled:
                w.core_in.append(Cancelled(leg=b.leg))
            else:
                w.core_in.append(Rejected(leg=b.leg))
            _run_core(w, tuple(w.core_in))
    elif k in (OutcomeKind.UNKNOWN, OutcomeKind.NOT_FOUND):
        w.reconcile.append((k.value, outcome.ref.client_id))      # books nothing: a NOT_FOUND is never "never filled"
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
        w.d['triggers'] = tuple(x for x in w.d['triggers'] if x is not t)
        d = w.draft(t.leg, purpose, OrderType.MARKET, t.qty, t.reason, op)
        w.submits.append(d)
        w.put(w.bindings() + [Binding(leg=t.leg, intent_id=d.intent_id, client_id=d.client_id, purpose=purpose,
                                      qty=t.qty, stop_price=None, trigger=t.price, reason=t.reason,
                                      state=BindState.SENT, exchange_order_id=None, filled=ZERO, executed=None,
                                      current=True, core_cancelled=False, replaces=None)])
    return _finish(w)


def on_candle(ds, candle, *, close_request=None, funding=None):
    """A CLOSED candle (time exit, trail, stop-crossed check), optionally with a strategy exit or a funding payment."""
    req(isinstance(candle, Candle), 'on_candle', 'a closed Candle')
    w = _W(ds)
    if ds.pos.stage is not Stage.DONE:
        _run_core(w, (), candle, close_request, funding)
    return _finish(w)


def set_mode(ds, mode, hold_kind=None):
    """The portfolio mode changed: release every held draft the NC-01 table now permits, in order."""
    w = _W(ds)
    w.d['mode'], w.d['hold_kind'] = EntriesMode(mode), hold_kind
    held, w.d['held'] = w.d['held'], ()
    for item in held:
        w.send(item)
    return _finish(w)


# ------------------------------------------------------------------------------------------------------ restart
def fold(plan, *, account_id, lot_id, entry_fee, events, lineage=(), route='classic'):
    """Rebuild the driver from its durable event log: ('fills', rows) / ('outcome', outcome, submit) / ('mark', price)
    / ('candle', candle, close_request, funding) / ('mode', mode, hold_kind). Returns every Drive, in order."""
    out = [start(plan, account_id=account_id, lot_id=lot_id, entry_fee=entry_fee, lineage=lineage, route=route)]
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
        else:
            raise ManagementError('fold.events', f'unknown event {kind!r}')
    return tuple(out)
