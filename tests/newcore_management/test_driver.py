"""ManagementDriver end to end through a FakeVenue-like stub (long and short): entry -> stop confirmed -> add held until
the stop is confirmed -> add -> TP1 partial -> TP1 complete -> net break-even replace (old stop kept until the new one is
confirmed) -> time exit -> stop released; plus dedupe, NOT_FOUND, HOLD and crash / restart replay identity."""
import dataclasses
import decimal
import itertools
from decimal import Decimal as D

import pytest

from mg_factories import GOLDEN_COSTS, H4, LONG, SHORT, T0, candle, plan, px
from newcore.domain import EntriesMode, HoldKind, IntentState, OrderType, OwnerKind, Purpose, make_id
from newcore.management import Leg, Stage, exit_ledger, realized_pnl
from newcore.management import driver as DR
from newcore.management.plan import market_fill
from newcore.ports.keys import derive_child_intent_id
from newcore.ports.venue import OrderOutcome, OrderRef, OutcomeKind, VenueFill

ACCT = make_id('acct', 7)
LOT = make_id('lot', 11)
DEC = make_id('dec', 13)


def mk_plan(side):
    return plan(side, add='99.02', tp1_frac='0.5', tp1_off='1', tp2_off='3', be=True, time_exit=4, cap='20',
                costs=GOLDEN_COSTS)


class Venue:
    """Fills market orders at the mark (adverse slippage, taker fee); holds STOP_MARKET orders until the mark crosses.
    Every answer is fed to the driver and logged, so `fold(log)` must land on the same state."""

    def __init__(self, p, *, confirm_stops=True, tp_partial=None):
        self.p, self.side = p, p.side
        self.mark = p.entry_price
        self.n = itertools.count(1)
        self.stops = {}                       # client_id -> (qty, price, xid)
        self.confirm_stops = confirm_stops
        self.tp_partial = tp_partial          # first TP1 market fills only this qty, then EXPIRED
        self.log = []
        self.ds = None
        self.all_submits, self.all_cancels = [], []

    def ref(self, d):
        return OrderRef(symbol=self.p.symbol, client_id=d.client_id, route=d.route)

    def at(self):
        return T0 + 1000 * len(self.log)

    def feed(self, kind, *args):
        self.log.append((kind,) + args)
        fn = {'fills': DR.on_fills, 'outcome': lambda ds, o, s: DR.on_outcome(ds, o, submit=s), 'mark': DR.on_mark,
              'candle': lambda ds, c, r, f: DR.on_candle(ds, c, close_request=r, funding=f),
              'mode': DR.set_mode}[kind]
        self.handle(fn(self.ds, *args))

    def handle(self, drive):
        self.ds = drive.state
        self.all_submits += drive.submits
        self.all_cancels += drive.cancels
        for d in drive.submits:
            DR.to_order_intent(d, decision_id=DEC, at_ms=self.at())          # a valid NC-01 PLANNED intent
            xid = f'x{next(self.n)}'
            if d.order_type is OrderType.STOP_MARKET:
                self.stops[d.client_id] = (d.qty, d.stop_price, xid)
                kind = OutcomeKind.KNOWN if self.confirm_stops else OutcomeKind.ACKNOWLEDGED
                self.feed('outcome', OrderOutcome(kind=kind, ref=self.ref(d), observed_at_ms=self.at(),
                                                  status='NEW' if kind is OutcomeKind.KNOWN else None,
                                                  exchange_order_id=xid if kind is OutcomeKind.KNOWN else None), True)
            else:
                q = d.qty
                if d.leg is Leg.TP1 and self.tp_partial is not None:
                    q, self.tp_partial = self.tp_partial, None
                self.market(d, q, xid)
        for c in drive.cancels:
            st = self.stops.pop(c.client_id, None)
            xid = st[2] if st else f'x{next(self.n)}'
            self.feed('outcome', OrderOutcome(kind=OutcomeKind.FINAL, ref=OrderRef(symbol=self.p.symbol,
                                                                                     client_id=c.client_id),
                                              observed_at_ms=self.at(), status='CANCELED', exchange_order_id=xid,
                                              executed_qty=D(0)), False)

    def market(self, d, q, xid):
        opening = d.purpose is Purpose.ADD
        fp = market_fill(self.mark, self.side, self.p.costs.slip, opening=opening)
        status = 'FILLED' if q == d.qty else 'EXPIRED'
        self.feed('outcome', OrderOutcome(kind=OutcomeKind.FINAL, ref=self.ref(d), observed_at_ms=self.at(),
                                          status=status, exchange_order_id=xid, executed_qty=q, avg_price=fp), True)
        self.feed('fills', (self.fill(xid, q, fp),))

    def fill(self, xid, q, price):
        return VenueFill(trade_id=f't{next(self.n)}', exchange_order_id=xid, symbol=self.p.symbol,
                         position_side=self.side.value, qty=q, price=price,
                         fee=self.p.costs.taker_fee * q * price, fee_asset='USDT', realized_pnl=D(0), maker=False,
                         at_ms=self.at())

    def move(self, mark):
        self.mark = mark
        for cid, (q, sp, xid) in list(self.stops.items()):
            if (mark <= sp) if self.side is LONG else (mark >= sp):
                del self.stops[cid]
                fp = market_fill(sp, self.side, self.p.costs.slip, opening=False)
                self.feed('outcome', OrderOutcome(kind=OutcomeKind.FINAL, ref=OrderRef(symbol=self.p.symbol,
                                                                                         client_id=cid),
                                                  observed_at_ms=self.at(), status='FILLED', exchange_order_id=xid,
                                                  executed_qty=q, avg_price=fp), False)
                self.feed('fills', (self.fill(xid, q, fp),))
        if self.ds.pos.stage is not Stage.DONE:
            self.feed('mark', mark)

    def confirm(self, client_id):
        q, sp, xid = self.stops[client_id]
        self.feed('outcome', OrderOutcome(kind=OutcomeKind.KNOWN, ref=OrderRef(symbol=self.p.symbol, client_id=client_id),
                                          observed_at_ms=self.at(), status='NEW', exchange_order_id=xid), False)


def boot(side, **kw):
    p = mk_plan(side)
    v = Venue(p, **kw)
    fee = GOLDEN_COSTS.taker_fee * p.entry_qty * p.entry_price
    v.entry_fee = fee
    v.handle(DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=fee))
    return p, v


def stops_of(v):
    return [d for d in v.all_submits if d.purpose is Purpose.PROTECT]


@pytest.mark.parametrize('side', (LONG, SHORT))
def test_end_to_end_with_add_held_partial_tp1_net_be_and_time_exit(side):
    p, v = boot(side, confirm_stops=False, tp_partial=D(2))
    first = stops_of(v)[0]
    assert first.intent_id == derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 0)
    assert first.owner_kind is OwnerKind.LOT and first.owner_id == LOT and first.reduce_only
    assert not DR.protected(v.ds) and DR.confirmed_coverage(v.ds) == 0
    # the add level is crossed while the stop is only ACKNOWLEDGED: the ADD stays held
    v.move(px(side, '99.0'))
    assert not any(d.purpose is Purpose.ADD for d in v.all_submits) and v.ds.pos.add_filled == 0
    v.confirm(first.client_id)
    assert DR.protected(v.ds)
    v.confirm_stops = True
    v.move(px(side, '98.99'))                                    # now the add fires
    adds = [d for d in v.all_submits if d.purpose is Purpose.ADD]
    assert len(adds) == 1 and adds[0].qty == D(5) and not adds[0].reduce_only
    assert v.ds.pos.qty == D(10) and v.ds.pos.stop.qty == D(10)
    assert first.client_id not in v.stops                         # replaced, and cancelled only after confirmation
    i_new = [k for k, d in enumerate(v.all_submits) if d.purpose is Purpose.PROTECT][1]
    assert any(c.intent_id == first.intent_id for c in v.all_cancels)
    # TP1: the first market fill is partial (2 of 5): no break-even yet; the rest re-arms and fills
    tp1 = v.ds.pos.tp1.price
    v.move(tp1)
    assert v.ds.pos.tp1_filled == D(2) and not v.ds.pos.tp1_done       # partial: no break-even
    v.move(tp1)                                                  # the re-armed rest fires on the next mark
    assert v.ds.pos.tp1_filled == D(5) and v.ds.pos.tp1_done
    reduces = [d for d in v.all_submits if d.purpose is Purpose.REDUCE]
    assert [d.qty for d in reduces][:2] == [D(5), D(3)]
    be = v.ds.pos.stop.price
    assert (be > v.ds.pos.basket_cost / v.ds.pos.basket_qty) if side is LONG else (be < v.ds.pos.basket_cost /
                                                                                      v.ds.pos.basket_qty)
    assert list(v.stops.values())[0][1] == be and len(v.stops) == 1       # the old stop is gone, the BE stop works
    # time exit at the close of candle 4 (counted from the entry candle)
    mid = (tp1 + be) / 2
    for i in range(4):
        v.feed('candle', candle(i, mid, mid + D('0.01'), mid - D('0.01'), mid, LONG), None, None)   # real prices
    closes = [d for d in v.all_submits if d.purpose is Purpose.CLOSE]
    assert len(closes) == 1 and closes[0].reason.value == 'exit.time_exit' and closes[0].qty == D(5)
    st = v.ds.pos
    assert st.stage is Stage.DONE and not v.stops and v.ds.bindings == () and st.racing == ()
    assert exit_ledger(p, st).realized_net == realized_pnl(p, st)
    # every intent id follows the (lot, purpose) lineage ordinals 0, 1, 2 ...
    for purpose in Purpose:
        ids = [d.intent_id for d in v.all_submits if d.purpose is purpose]
        assert ids == [derive_child_intent_id(ACCT, LOT, purpose, k) for k in range(len(ids))]
    # crash / restart: the durable log folds to the identical state at every point
    replay = DR.fold(p, account_id=ACCT, lot_id=LOT, entry_fee=v.entry_fee, events=v.log)
    assert replay[-1].state == v.ds
    assert sum((list(x.submits) for x in replay), []) == v.all_submits
    with decimal.localcontext(decimal.Context(prec=4, rounding=decimal.ROUND_UP, traps=[])):
        hostile = DR.fold(p, account_id=ACCT, lot_id=LOT, entry_fee=v.entry_fee, events=v.log)
    assert repr(hostile[-1].state) == repr(v.ds)                  # no ambient decimal context in the driver either
    _ = i_new


@pytest.mark.parametrize('side', (LONG, SHORT))
def test_duplicate_trades_and_not_found_book_nothing(side):
    p, v = boot(side)
    v.move(px(side, '98.99'))
    xid_fill = next(e for e in v.log if e[0] == 'fills')[1]
    before = v.ds
    again = DR.on_fills(v.ds, xid_fill)
    assert again.state.pos == before.pos and again.submits == ()
    stop = v.ds.bindings[0]
    nf = OrderOutcome(kind=OutcomeKind.NOT_FOUND, ref=OrderRef(symbol=p.symbol, client_id=stop.client_id),
                      observed_at_ms=T0, error_code=-2013)
    r = DR.on_outcome(v.ds, nf, submit=False)
    assert r.state.pos == v.ds.pos and r.reconcile == (('not_found', stop.client_id),)
    stray = VenueFill(trade_id='zzz', exchange_order_id='nope', symbol=p.symbol, position_side=side.value, qty=D(1),
                      price=D(100), fee=D(0), fee_asset='USDT', realized_pnl=D(0), maker=False, at_ms=T0)
    r = DR.on_fills(v.ds, (stray,))
    assert r.state.pos == v.ds.pos and r.reconcile == (('unmatched_fill', 'zzz'),)


@pytest.mark.parametrize('side', (LONG, SHORT))
def test_hold_lets_protection_through_and_holds_management(side):
    p, v = boot(side)
    v.feed('mode', EntriesMode.HOLD, HoldKind.NORMAL)
    n = len(v.all_submits)
    v.move(px(side, '98.99'))                                     # the add level: no ADD in HOLD
    v.move(v.ds.pos.tp1.price)                                    # a target level: no TP in HOLD (Op.MANAGE)
    assert len(v.all_submits) == n
    for i in range(4):                                            # the time exit is management: held
        v.feed('candle', candle(i, '100', '100.01', '99.99', '100', side), None, None)
    assert v.ds.held and all(d.purpose is Purpose.CLOSE for d in v.ds.held)
    assert v.ds.pos.closing == p.entry_qty
    v.feed('mode', EntriesMode.ACTIVE, None)                      # released in order
    assert v.ds.held == () and v.ds.pos.stage is Stage.DONE


def test_a_draft_is_a_valid_nc01_intent():
    p, v = boot(LONG)
    for d in v.all_submits:
        it = DR.to_order_intent(d, decision_id=DEC, at_ms=T0)
        assert it.state is IntentState.PLANNED and it.owner_kind is OwnerKind.LOT and it.reduce_only == d.reduce_only
    _ = dataclasses, H4
