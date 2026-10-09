"""Step-0 VenuePort / BarSource value types and Protocol conformance (STEP0_INTERFACE.md sections 3-5)."""
from decimal import Decimal as D

import pytest

from newcore.ports import bars as B
from newcore.ports import journal as J
from newcore.ports import keys as K
from newcore.ports import venue as V
from newcore.ports.values import PortValueError

T = 1759924800000
CID = K.client_id_for('int_' + '7' * 32)
REF = V.OrderRef(symbol='SOLUSDT', client_id=CID)
ALGO = V.OrderRef(symbol='SOLUSDT', client_id=K.client_id_for('int_' + '7' * 32, 'algo'), route='algo')


def outcome(kind, **kw):
    return V.OrderOutcome(kind=kind, ref=REF, observed_at_ms=T, **kw)


# ------------------------------------------------------------------------------------------------ order values
def test_outcome_kinds_equal_the_transport_vocabulary():
    assert [k.value for k in V.OutcomeKind] == ['known', 'final', 'acknowledged', 'rejected', 'unknown', 'not_found']
    assert [k.value for k in V.ReadKind] == ['ok', 'rejected', 'unknown']


def test_valid_outcomes():
    outcome('final', status='FILLED', exchange_order_id='42', executed_qty=D('1.5'), avg_price=D('150.2'))
    outcome('final', status='CANCELED', exchange_order_id='42', executed_qty=D('0'))
    outcome('known', status='NEW', exchange_order_id='42')
    outcome('rejected', error_code=-4120, detail='algo_route')
    outcome('unknown', detail='timeout')
    outcome('unknown', error_code=-1007)
    outcome('not_found', error_code=-2013)
    outcome('acknowledged')


@pytest.mark.parametrize('kind,kw', [
    ('not_found', dict(error_code=-2013, executed_qty=D('0'))),           # not-found books nothing (NC-01 inv. 7)
    ('unknown', dict(executed_qty=D('1'))),
    ('known', dict(status='PARTIALLY_FILLED', exchange_order_id='42', executed_qty=D('1'))),   # not trusted
    ('final', dict(status='FILLED', exchange_order_id='42')),             # final decides the executed qty
    ('final', dict(status='FILLED', exchange_order_id='42', executed_qty=D('1'))),   # executed without a price
    ('final', dict(status='CANCELED', exchange_order_id='42', executed_qty=D('0'), avg_price=D('1'))),
    ('final', dict(status='FILLED', exchange_order_id='42', executed_qty=1.0, avg_price=D('1'))),   # float
    ('rejected', dict()),                                                 # no error code
    ('not_found', dict()),
    ('acknowledged', dict(error_code=-1)),
    ('known', dict(status='NEW')),                                        # no exchange order id
    ('unknown', dict(detail='a long free-text exception message that is not a token')),
    ('final ', dict()),
])
def test_invalid_outcomes(kind, kw):
    with pytest.raises((PortValueError, ValueError)):
        outcome(kind, **kw)


def test_read_outcome_unknown_is_never_empty():
    assert V.ReadOutcome(kind='ok', observed_at_ms=T, value=()).value == ()      # a trusted empty
    for bad in (dict(kind='unknown', value=()), dict(kind='rejected', value=(), error_code=-1),
                dict(kind='ok', value=None), dict(kind='ok', value=[]), dict(kind='rejected'),
                dict(kind='unknown', error_code=-1)):
        with pytest.raises(PortValueError):
            V.ReadOutcome(observed_at_ms=T, **bad)


def test_order_requests():
    V.MarketOrder(ref=REF, position_side='SHORT', qty=D('0.1'), reduce=True)
    V.StopOrder(ref=ALGO, position_side='LONG', qty=D('0.1'), stop_price=D('140'))
    for call in (lambda: V.MarketOrder(ref=ALGO, position_side='LONG', qty=D('1'), reduce=False),
                 lambda: V.MarketOrder(ref=REF, position_side='BOTH', qty=D('1'), reduce=False),
                 lambda: V.MarketOrder(ref=REF, position_side='LONG', qty=D('0'), reduce=False),
                 lambda: V.MarketOrder(ref=REF, position_side='LONG', qty=D('1'), reduce=1),
                 lambda: V.StopOrder(ref=REF, position_side='LONG', qty=D('1'), stop_price=D('NaN')),
                 lambda: V.OrderRef(symbol='SOLUSDT', client_id='x' * 37),
                 lambda: V.OrderRef(symbol='SOLUSDT', client_id=CID, route='ALGO')):
        with pytest.raises(PortValueError):
            call()


def test_read_values():
    V.VenuePosition(symbol='SOLUSDT', side='SHORT', qty=D('0'), entry_price=D('0'))
    V.VenueOrder(ref=V.OrderRef(symbol='SOLUSDT', client_id='web_foreign1'), exchange_order_id='9', position_side='LONG',
                 reduce=True, order_type='STOP_MARKET', status='NEW', qty=D('0'), close_position=True,
                 stop_price=D('120'))
    V.VenueFill(trade_id='1', exchange_order_id='9', symbol='SOLUSDT', position_side='LONG', qty=D('1'),
                price=D('150'), fee=D('-0.01'), fee_asset='USDT', realized_pnl=D('0'), maker=False, at_ms=T)
    with pytest.raises(PortValueError):
        V.VenuePosition(symbol='SOLUSDT', side='LONG', qty=D('-1'), entry_price=D('0'))      # no signed quantities
    with pytest.raises(PortValueError):
        V.VenueOrder(ref=REF, exchange_order_id='9', position_side='LONG', reduce=True, order_type='STOP_MARKET',
                     status='NEW', qty=D('0'), close_position=False, stop_price=None)


# ------------------------------------------------------------------------------------------------ bars
H4 = 14_400_000


def bar(open_ms, tf=H4, **kw):
    v = dict(open=D('100'), high=D('110'), low=D('90'), close=D('105'), volume=D('1'))
    return B.Bar(open_ms=open_ms, close_ms=open_ms + tf, **{**v, **kw})


def test_closed_bars_accepts_contiguous_closed_candles():
    bars = tuple(bar(T - H4 * n) for n in range(3, 0, -1))
    assert B.check_closed_bars(bars, H4, as_of_ms=T) == bars
    assert B.check_closed_bars((), H4, as_of_ms=T) == ()


@pytest.mark.parametrize('bars,as_of', [
    ((bar(T - H4), bar(T)), T),                         # the forming candle (closes at T + 4h)
    ((bar(T - 3 * H4), bar(T - H4)), T),                # gap
    ((bar(T - H4), bar(T - 2 * H4)), T),                # disorder
    ((bar(T - H4 + 1),), T + H4),                       # misaligned open
    ((bar(T - H4, tf=H4 - 1),), T),                     # close is not open + tf
    ([bar(T - H4)], T),                                 # not a tuple
])
def test_closed_bars_rejects(bars, as_of):
    with pytest.raises(PortValueError):
        B.check_closed_bars(bars, H4, as_of_ms=as_of)


def test_bar_values_are_strict():
    for kw in (dict(high=D('99')), dict(low=D('106')), dict(open=100.0), dict(volume=D('-1'))):
        with pytest.raises(PortValueError):
            bar(T, **kw)
    with pytest.raises(PortValueError):
        B.Bar(open_ms=T, close_ms=T, open=D('1'), high=D('1'), low=D('1'), close=D('1'), volume=D('0'))
    for args in (('SOLUSDT', 7 * 60_000, T, 10), ('SOLUSDT', H4, T, 0), ('sol', H4, T, 1), ('SOLUSDT', H4, 1.0, 1)):
        with pytest.raises(PortValueError):
            B.check_request(*args)


# ------------------------------------------------------------------------------------------------ Protocol conformance
class StubVenue:
    def submit_market(self, order):
        return V.OrderOutcome(kind='final', ref=order.ref, observed_at_ms=T, status='FILLED', exchange_order_id='1',
                              executed_qty=order.qty, avg_price=D('150'))

    def submit_stop(self, order):
        return V.OrderOutcome(kind='known', ref=order.ref, observed_at_ms=T, status='NEW', exchange_order_id='2')

    def cancel(self, ref):
        return V.OrderOutcome(kind='not_found', ref=ref, observed_at_ms=T, error_code=-2011)

    def query(self, ref):
        return V.OrderOutcome(kind='unknown', ref=ref, observed_at_ms=T, detail='timeout')

    def positions(self, symbol=None):
        return V.ReadOutcome(kind='ok', observed_at_ms=T, value=())

    def open_orders(self, symbol=None):
        return V.ReadOutcome(kind='unknown', observed_at_ms=T)

    def fills(self, symbol, exchange_order_id):
        return V.ReadOutcome(kind='rejected', observed_at_ms=T, error_code=-1121)

    def order_by_id(self, symbol, exchange_order_id):
        return V.ReadOutcome(kind='rejected', observed_at_ms=T, error_code=-2013)


class StubBars:
    def closed_bars(self, symbol, tf_ms, *, as_of_ms, limit):
        B.check_request(symbol, tf_ms, as_of_ms, limit)
        return V.ReadOutcome(kind='ok', observed_at_ms=as_of_ms,
                             value=B.check_closed_bars((bar(as_of_ms - H4),), tf_ms, as_of_ms))


class Incomplete:
    def submit_market(self, order):
        return None


def test_trivial_stubs_conform_to_the_ports():
    v, b = StubVenue(), StubBars()
    assert isinstance(v, V.VenuePort) and isinstance(b, B.BarSource) and not isinstance(Incomplete(), V.VenuePort)
    assert not isinstance(v, J.JournalPort)                        # the journal has its own contract suite
    order = V.MarketOrder(ref=REF, position_side='LONG', qty=D('1'), reduce=False)
    assert v.submit_market(order).executed_qty == D('1')
    assert v.cancel(REF).executed_qty is None and v.open_orders().value is None
    assert b.closed_bars('SOLUSDT', H4, as_of_ms=T, limit=1).value[0].close_ms == T
