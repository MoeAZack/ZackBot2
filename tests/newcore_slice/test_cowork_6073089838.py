"""Cowork 6073089838 (composition risk, S1 + the real TestnetVenue.order_by_id): our ALGO emergency stop triggers and
Binance creates a classic CHILD order. If that child carries a foreign / system-assigned client id, the order-by-id
record is "not ours" - and at f9d774a the runner read that as PROVEN not an emergency order: _emergency_filled = 0
instead of 1, then a close of 5 against a venue position of 5.

Fail closed, without guessing what id the child carries: a record whose client id is not ours is PROVEN foreign only
when it is an OPENING order - its side increases the position, reduceOnly false, closePosition false, and (where the
record carries them) type / origType not conditional (STOP*, TAKE_PROFIT*, TRAILING*). A closing-side or conditional
record that is not ours, or one missing those fields, is ownership UNKNOWN: resting protection kept, nothing sent,
incident, HOLD.
"""
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode
from newcore.ports.venue import OrderRef, ReadKind, ReadOutcome, VenueOrderRecord
from test_codex_6071659449 import ORDERS, SYM, partial_emergency, position, run_on, stops

pytestmark = pytest.mark.usefixtures('journal_kind')
SIDES = ('LONG', 'SHORT')
MODES = ('restart', 'restart-store-down', 'store-lost')            # store-lost: the same process, no restart
CLOSING = {'LONG': 'SELL', 'SHORT': 'BUY'}
OPENING = {'LONG': 'BUY', 'SHORT': 'SELL'}


def child_record(w, o, route='classic', **fields):
    """The order-by-id lookup of the triggered algo stop answers its classic CHILD with a foreign / system client id.
    `fields`: the record's optional opening-proof fields (none given = an adapter that does not populate them)."""
    inner = w.venue.order_by_id

    def order_by_id(symbol, eoid):
        r = inner(symbol, eoid)
        if eoid != o.exchange_order_id:
            return r
        x, = r.value
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=r.observed_at_ms, value=(VenueOrderRecord(
            ref=OrderRef(symbol=SYM, client_id='autoclose-' + eoid, route=route), exchange_order_id=x.exchange_order_id,
            position_side=x.position_side, status='FILLED' if x.executed_qty == x.orig_qty else 'CANCELED',
            orig_qty=x.orig_qty, executed_qty=x.executed_qty, **fields),))
    w.port.order_by_id = order_by_id


def assert_unknown_hold(w, lot, side, mode):
    assert w.runner._emergency_filled(SYM, side) is None
    before, sent, venue = run_on(w, side)
    r = w.runner
    assert sent == [], [(x.order_type, x.qty) for x in sent]              # no close of 5, no stop / resize
    assert [(x.ref.client_id, x.qty) for x in stops(w, side)] == before   # resting protection kept
    assert position(w, side) == venue
    assert r.mode is EntriesMode.HOLD
    assert any('order-id lookup' in t for _, t in r.incidents)
    if mode == 'restart':
        assert lot.lot_id in r._own_unknown


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('order', ORDERS, ids=('req2-1row', 'req3-2rows'))
@pytest.mark.parametrize('foreign', ('1', '2'), ids=('equal', 'non-equal'))
@pytest.mark.parametrize('child', ('market-child', 'stop-child', 'close-position', 'no-fields'))
def test_triggered_algo_child_with_a_foreign_id_is_unknown(side, mode, order, foreign, child):
    """Cowork's scenario: the algo stop's child (closing side) carries a foreign client id -> UNKNOWN / HOLD."""
    w, lot, o = partial_emergency(side, 'algo', order, 'cancelled', foreign, mode, 'ok')
    fields = {'market-child': dict(side=CLOSING[side], reduce_only=True, close_position=False, order_type='MARKET',
                                   orig_type='STOP_MARKET'),
              'stop-child': dict(side=CLOSING[side], reduce_only=False, close_position=False,
                                 order_type='STOP_MARKET', orig_type='STOP_MARKET'),
              'close-position': dict(side=CLOSING[side], reduce_only=False, close_position=True,
                                     order_type='MARKET', orig_type='STOP_MARKET'),
              'no-fields': {}}[child]
    child_record(w, o, **fields)
    assert_unknown_hold(w, lot, side, mode)


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('variant', ('reduce-only', 'reduce-only-opening-side', 'conditional-type',
                                     'conditional-origtype', 'trailing', 'take-profit', 'side-missing',
                                     'reduce-missing', 'close-missing', 'side-bogus'))
def test_a_foreign_closing_or_conditional_record_is_unknown(side, variant):
    """A foreign reduce-only close, a conditional type, or a required field missing: never proven foreign."""
    w, lot, o = partial_emergency(side, 'classic', ORDERS[0], 'cancelled', '1', 'restart', 'ok')
    f = dict(side=OPENING[side], reduce_only=False, close_position=False, order_type='MARKET', orig_type='MARKET')
    if variant == 'reduce-only':
        f.update(side=CLOSING[side], reduce_only=True)
    elif variant == 'reduce-only-opening-side':
        f.update(reduce_only=True)
    elif variant == 'conditional-type':
        f.update(order_type='STOP')
    elif variant == 'conditional-origtype':
        f.update(orig_type='STOP_MARKET')
    elif variant == 'trailing':
        f.update(orig_type='TRAILING_STOP_MARKET')
    elif variant == 'take-profit':
        f.update(order_type='TAKE_PROFIT_MARKET')
    elif variant == 'side-bogus':
        f.update(side='BOTH')
    else:
        f.pop({'side-missing': 'side', 'reduce-missing': 'reduce_only', 'close-missing': 'close_position'}[variant])
    child_record(w, o, **f)
    assert_unknown_hold(w, lot, side, 'restart')


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('types', (('MARKET', 'MARKET'), ('LIMIT', 'LIMIT'), (None, None)))
def test_a_foreign_opening_add_stays_proven_foreign(side, types):
    """Control: a foreign OPENING record (increases the position, not reduce-only / closePosition, not conditional;
    type / origType optional) is still proven foreign - existing behaviour kept."""
    w, lot, o = partial_emergency(side, 'classic', ORDERS[0], 'cancelled', '1', 'restart', 'ok')
    child_record(w, o, side=OPENING[side], reduce_only=False, close_position=False, order_type=types[0],
                 orig_type=types[1])
    assert w.runner._emergency_filled(SYM, side) == D('0')


@pytest.mark.parametrize('side', SIDES)
def test_fake_venue_populates_the_opening_fields(side):
    """FakeVenue's records carry side / reduceOnly / closePosition / type / origType (TestnetVenue must too)."""
    w, lot, o = partial_emergency(side, 'algo', ORDERS[0], 'cancelled', '1', 'restart', 'ok')
    x, = w.venue.order_by_id(SYM, o.exchange_order_id).value
    assert (x.side, x.reduce_only, x.close_position, x.order_type, x.orig_type) == (
        CLOSING[side], True, False, 'STOP_MARKET', 'STOP_MARKET')
    add = [f for f in w.venue._fills if f.exchange_order_id.startswith('foreign-')][-1]
    y, = w.venue.order_by_id(SYM, add.exchange_order_id).value
    assert (y.side, y.reduce_only, y.close_position, y.order_type, y.orig_type) == (
        OPENING[side], False, False, 'MARKET', 'MARKET')


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('emergency', ('classic', 'algo'))
def test_an_opening_shaped_algo_route_record_is_unknown(side, mode, emergency):
    """Codex 6073909317 (numeric id collision): the classic lookup of a trade-row order id answers -2013 and the
    algo fallback returns an UNRELATED algo order with the same numeric id, opening-shaped and not ours. Only a
    classic-route record proves foreign: an algo-route answer is UNKNOWN / HOLD, whatever its fields say."""
    w, lot, o = partial_emergency(side, emergency, ORDERS[0], 'cancelled', '1', mode, 'ok')
    child_record(w, o, route='algo', side=OPENING[side], reduce_only=False, close_position=False,
                 order_type='MARKET', orig_type='MARKET')
    assert_unknown_hold(w, lot, side, mode)
