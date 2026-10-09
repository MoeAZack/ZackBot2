"""Codex 6071659449 (P1 at 59f2961): a PARTIALLY filled previous-process emergency order is attributed by its exchange
order id, never by its filled quantity.

Journaled lot 5 ours. An earlier process's deterministic zbn1e emergency stop REQUESTED more than it filled (2 -> 1, or
3 -> 1 over two partial rows), then was cancelled / expired / is still working. A foreign same-side add restores an
equal (5) or a non-equal (6) venue quantity; the journal stop is gone. The client id re-derived from the filled
quantity is not the real one (that was encoded from the requested quantity), so ownership is proven only by the venue-port lookup
of the order by its exchange order id (Binance order query by orderId): its original client id must be one of our
emergency ids for its ORIGINAL quantity and its executed quantity must equal the trade rows.
  lookup OK        -> our emergency fills = 1, own 4: never a stop / close of 5, HOLD, loud;
  lookup NOT_FOUND / UNKNOWN / unavailable -> ownership UNKNOWN: resting protection kept, nothing sent, incident, HOLD.
Both sides, classic and algo routes, with and without a restart (writable store, store down at boot, store lost
in-process).
"""
from decimal import Decimal as D

import pytest

from newcore.adapters.fake_venue import FakeVenue
from newcore.domain import EntriesMode
from newcore.ports.venue import OrderRef, ReadKind, ReadOutcome, VenueOrderRecord, VenuePort
from newcore.runner import InjectedSignals, ids
from slice_helpers import H4, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')
ORDERS = (('2', ('1',)), ('3', ('0.5', '0.5')))          # (requested, partial rows): filled 1 either way
ENDS = ('cancelled', 'expired', 'working')
MODES = ('restart', 'restart-store-down', 'store-lost')


def at(bar):
    return T0 + (bar + 1) * H4


def position(w, side):
    return sum((p.qty for p in w.venue.positions().value if p.side == side), D(0))


def stops(w, side):
    return [o for o in w.venue.open_orders().value if o.reduce and o.position_side == side
            and o.order_type == 'STOP_MARKET']


def lookup_answers(w, eoid, how):
    """The order-by-id lookup of `eoid` answers `how` (ok = the fake's own answer)."""
    if how == 'ok':
        return
    inner = w.venue.order_by_id

    def order_by_id(symbol, exchange_order_id):
        if exchange_order_id != eoid:
            return inner(symbol, exchange_order_id)
        if how == 'not_found':
            return ReadOutcome(kind=ReadKind.REJECTED, observed_at_ms=w.venue.now_ms, error_code=-2013)
        return ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=w.venue.now_ms, detail='timeout')
    w.port.order_by_id = order_by_id


def partial_emergency(side, route, order, end, foreign, mode, lookup):
    requested, rows = order
    w = World(flat_bars(30), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    assert lot.qty == D('5')
    v = w.venue
    cid = ids.emergency_stop_client_id(w.runner.acct, SYM, side, D(requested), 0)
    o = v._new_order(OrderRef(symbol=SYM, client_id=cid, route=route), 'STOP_MARKET', side, D(requested), True,
                     D('90') if side == 'LONG' else D('110'))
    for q in rows:                                                        # the stop triggered; partial fills
        v._execute_part(o, D(q), D('100'), at_ms=v.now_ms)
    assert o.executed == D('1') and o.status == 'NEW'
    if end != 'working':
        o.status = 'CANCELED' if end == 'cancelled' else 'EXPIRED'
    assert position(w, side) == D('4')
    v.inject_position(SYM, side, D(foreign), D('100'))                    # foreign same-side add
    v.external_cancel(lot.live_stop.intent.client_order_id)               # the journal stop is gone
    if mode == 'restart':
        w.restart()
    elif mode == 'restart-store-down':
        w.journal.fail_writes(10 ** 9)
        w.restart(hard_hold='test: store down at boot')
    else:
        w.journal.fail_writes(10 ** 9)
        w.runner.store_unavailable('test: ENOSPC')
    lookup_answers(w, o.exchange_order_id, lookup)
    return w, lot, o


def run_on(w, side):
    before = [(o.ref.client_id, o.qty) for o in stops(w, side)]
    n = len(w.venue.orders_submitted())
    venue = position(w, side)
    for b in (7, 8, 9):
        w.run(b)
    return before, list(w.venue.orders_submitted()[n:]), venue


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('route', ('classic', 'algo'))
@pytest.mark.parametrize('order', ORDERS, ids=('req2-1row', 'req3-2rows'))
@pytest.mark.parametrize('end', ENDS)
@pytest.mark.parametrize('foreign', ('1', '2'), ids=('equal', 'non-equal'))
@pytest.mark.parametrize('mode', MODES)
def test_lookup_ok_proves_the_partial_emergency_fill(side, route, order, end, foreign, mode):
    w, lot, o = partial_emergency(side, route, order, end, foreign, mode, 'ok')
    assert w.runner._emergency_filled(SYM, side) == D('1')                # the real id was found by order id
    before, sent, venue = run_on(w, side)
    r = w.runner
    assert not [x for x in sent if x.order_type == 'MARKET'], [(x.order_type, x.qty) for x in sent]
    assert all(x.qty <= D('4') for x in sent), [(x.order_type, x.qty) for x in sent]   # never a stop of 5
    assert sum((x.qty for x in sent), D(0)) <= D('4')
    assert position(w, side) == venue
    assert r.mode is EntriesMode.HOLD
    assert lot.lot_id not in r._own_unknown


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('route', ('classic', 'algo'))
@pytest.mark.parametrize('order', ORDERS, ids=('req2-1row', 'req3-2rows'))
@pytest.mark.parametrize('end', ENDS)
@pytest.mark.parametrize('foreign', ('1', '2'), ids=('equal', 'non-equal'))
@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('lookup', ('not_found', 'unknown'))
def test_lookup_not_proven_is_ownership_unknown(side, route, order, end, foreign, mode, lookup):
    w, lot, o = partial_emergency(side, route, order, end, foreign, mode, lookup)
    assert w.runner._emergency_filled(SYM, side) is None
    before, sent, venue = run_on(w, side)
    r = w.runner
    assert sent == [], [(x.order_type, x.qty) for x in sent]              # no stop / close / resize
    assert [(x.ref.client_id, x.qty) for x in stops(w, side)] == before   # resting protection kept
    assert position(w, side) == venue
    assert r.mode is EntriesMode.HOLD
    assert any('order-id lookup' in t for _, t in r.incidents)
    if mode == 'restart':
        assert lot.lot_id in r._own_unknown


@pytest.mark.parametrize('side', SIDES)
def test_lookup_unavailable_is_ownership_unknown(side):
    """An adapter without the lookup (an older TestnetVenue): nothing proves the order - UNKNOWN, never foreign."""
    w, lot, o = partial_emergency(side, 'classic', ORDERS[0], 'cancelled', '1', 'restart', 'ok')
    w.port.order_by_id = None
    assert w.runner._emergency_filled(SYM, side) is None
    _, sent, _ = run_on(w, side)
    assert sent == [] and w.runner.mode is EntriesMode.HOLD


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('lie', ('executed', 'client', 'side', 'eoid'))
def test_a_mismatched_lookup_is_ownership_unknown(side, lie):
    """The record disagrees with the trade rows (executed qty), names another id / side, or another order."""
    w, lot, o = partial_emergency(side, 'classic', ORDERS[0], 'cancelled', '1', 'restart', 'ok')
    inner = w.venue.order_by_id

    def order_by_id(symbol, eoid):
        r = inner(symbol, eoid)
        if eoid != o.exchange_order_id:
            return r
        x, = r.value
        kw = dict(ref=x.ref, exchange_order_id=x.exchange_order_id, position_side=x.position_side, status=x.status,
                  orig_qty=x.orig_qty, executed_qty=x.executed_qty)
        if lie == 'executed':
            kw['executed_qty'] = D('2')
        elif lie == 'client':
            kw['ref'] = OrderRef(symbol=SYM, client_id=ids.emergency_stop_client_id('other', SYM, side, D('2'), 0))
        elif lie == 'side':
            kw['position_side'] = 'SHORT' if side == 'LONG' else 'LONG'
        else:
            kw['exchange_order_id'] = '424242'
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=r.observed_at_ms, value=(VenueOrderRecord(**kw),))
    w.port.order_by_id = order_by_id
    assert w.runner._emergency_filled(SYM, side) is None
    _, sent, _ = run_on(w, side)
    assert sent == [] and w.runner.mode is EntriesMode.HOLD


@pytest.mark.parametrize('side', SIDES)
def test_a_foreign_order_proven_by_lookup_stays_foreign(side):
    """Control: a non-emergency order's rows (a foreign add) are proven foreign by the lookup - no UNKNOWN."""
    w = World(flat_bars(30), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    v = w.venue
    o = v._new_order(OrderRef(symbol=SYM, client_id='manual-add-1'), 'MARKET', side, D('2'), False, None)
    v._execute(o, D('2'), D('100'), at_ms=v.now_ms)
    w.restart()
    assert w.runner._emergency_filled(SYM, side) == D('0')


def test_fake_venue_order_by_id():
    from slice_helpers import flat_bars as fb
    v = FakeVenue({SYM: fb(5)}, H4)
    assert isinstance(v, VenuePort)
    o = v._new_order(OrderRef(symbol=SYM, client_id='zbn1e-' + 'a' * 26, route='algo'), 'STOP_MARKET', 'LONG',
                     D('2'), True, D('90'))
    v._apply_fill(SYM, 'LONG', D('5'), D('100'), reduce=False, eoid='x', at_ms=v.now_ms, fee=D(0))
    v._execute_part(o, D('1'), D('100'), at_ms=v.now_ms)
    r = v.order_by_id(SYM, o.exchange_order_id)
    assert r.kind is ReadKind.OK
    x, = r.value
    assert (x.ref.client_id, x.ref.route, x.orig_qty, x.executed_qty, x.status) == (
        o.ref.client_id, 'algo', D('2'), D('1'), 'PARTIALLY_FILLED')
    nf = v.order_by_id(SYM, '999999')
    assert nf.kind is ReadKind.REJECTED and nf.error_code == -2013
    assert v.order_by_id('BTCUSDT', o.exchange_order_id).kind is ReadKind.REJECTED
