"""ScriptedSignals (keyed, deterministic, never back-dated) and the harness seams (bound + ledger, port faults, HTTP
faults classified on the request, synthetic refusals never sent)."""
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from newcore.ports import venue as P
from newcore.ports.bars import Bar
from newcore.runner.signals import CLOSE, ENTER
from newcore.tnet.seams import BoundedPort, BoundExceeded, HttpFaults, PortFaults, classify
from newcore.venue.wire import WireTimeout

T0 = 1_759_917_600_000


def bars(n, px='100', t0=T0):
    p = D(px)
    return tuple(Bar(open_ms=t0 + i * 60_000, close_ms=t0 + (i + 1) * 60_000, open=p, high=p + 1, low=p - 1, close=p,
                     volume=D(1)) for i in range(n))


def sig(**kw):
    from newcore.tnet.signals import ScriptedSignals
    return ScriptedSignals(nonce=kw.pop('nonce', '0a1b2c3d'), stop=kw.pop('stop', {'pct': '1'}), **kw)


@pytest.mark.parametrize('nonce', ['0A1B2C3D', '0a1b2c3', '0a1b2c3d4', 'zzzzzzzz', None])
def test_nonce_must_be_8_lowercase_hex(nonce):
    with pytest.raises(ValueError):
        sig(nonce=nonce)


@pytest.mark.parametrize('stop', [{}, {'pct': '1', 'atr': '1'}, {'ticks': '3'}, None])
def test_stop_must_be_pct_or_atr(stop):
    with pytest.raises(ValueError):
        sig(stop=stop)


def test_name_carries_the_run_nonce_and_fits_the_strategy_grammar():
    from newcore.ports.keys import strategy_instance
    s = sig()
    assert s.name == 'tnet_0a1b2c3d' and s.tf_label == '1m'
    assert strategy_instance(s.name, s.tf_label) == 'tnet_0a1b2c3d@1m'


def test_nothing_armed_nothing_fires():
    assert sig().decide('SOLUSDT', bars(20), bars(20)[-1].close_ms) == ()


def test_armed_action_fires_once_on_the_next_candle_with_pct_distance():
    s = sig(stop={'pct': '2'})
    b = bars(20)
    s.decide('SOLUSDT', b[:-1], b[-2].close_ms)
    s.arm('SOLUSDT', ENTER, 'LONG')
    out = s.decide('SOLUSDT', b, b[-1].close_ms)
    assert len(out) == 1 and out[0].action == ENTER and out[0].side == 'LONG'
    assert out[0].candle_close_ms == b[-1].close_ms and out[0].stop_distance == D('2')
    assert s.decide('SOLUSDT', b, b[-1].close_ms) == out                      # same candle: same signal (re-derive)
    assert s.decide('SOLUSDT', bars(21), bars(21)[-1].close_ms) == ()         # consumed
    assert s.last_close['SOLUSDT'] == D('100')


def test_never_fires_on_an_older_candle():
    s = sig()
    b = bars(20)
    s.decide('SOLUSDT', b, b[-1].close_ms)
    s.arm('SOLUSDT', ENTER, 'LONG')
    assert s.decide('SOLUSDT', b[:-3], b[-4].close_ms) == ()                  # an old window: not back-dated
    assert s.decide('SOLUSDT', b, b[-1].close_ms) == ()                       # the same newest candle: already seen
    assert s.armed('SOLUSDT') == ((ENTER, 'LONG'),)
    assert s.decide('SOLUSDT', bars(21), bars(21)[-1].close_ms)[0].action == ENTER


def test_closes_come_first_and_carry_no_distance():
    s = sig()
    s.arm('SOLUSDT', ENTER, 'SHORT')
    s.arm('SOLUSDT', CLOSE, 'LONG')
    out = s.decide('SOLUSDT', bars(20), 0)
    assert [(x.action, x.side) for x in out] == [(CLOSE, 'LONG'), (ENTER, 'SHORT')]
    assert out[0].stop_distance is None and out[1].stop_distance == D('1')


def test_atr_distance_needs_fifteen_candles_else_stays_armed():
    s = sig(stop={'atr': '1.5'})
    s.arm('SOLUSDT', ENTER, 'LONG')
    assert s.decide('SOLUSDT', bars(14), 0) == () and s.starved == 1
    assert s.armed('SOLUSDT') == ((ENTER, 'LONG'),)
    out = s.decide('SOLUSDT', bars(15), 0)
    assert out[0].stop_distance == D('3.0')                                   # ATR of h-l = 2, x 1.5


def test_a_close_alone_needs_no_distance():
    s = sig(stop={'atr': '1'})
    s.arm('SOLUSDT', CLOSE, 'LONG')
    assert s.decide('SOLUSDT', bars(3), 0)[0].action == CLOSE


def test_bad_arm_is_refused():
    with pytest.raises(ValueError):
        sig().arm('SOLUSDT', 'add', 'LONG')
    with pytest.raises(ValueError):
        sig().arm('SOLUSDT', ENTER, 'BOTH')


# ------------------------------------------------------------------------------------------------ seams
class Inner:
    now_ms = T0

    def __init__(self):
        self.calls = []

    def submit_market(self, order):
        self.calls.append(('market', order.ref.client_id))
        return P.OrderOutcome(kind=P.OutcomeKind.FINAL, ref=order.ref, observed_at_ms=T0, status='FILLED',
                              exchange_order_id='1', executed_qty=order.qty, avg_price=D('100'))

    def submit_stop(self, order):
        self.calls.append(('stop', order.ref.client_id))
        return P.OrderOutcome(kind=P.OutcomeKind.KNOWN, ref=order.ref, observed_at_ms=T0, status='NEW',
                              exchange_order_id='2')

    def cancel(self, ref):
        self.calls.append(('cancel', ref.client_id))
        return P.OrderOutcome(kind=P.OutcomeKind.FINAL, ref=ref, observed_at_ms=T0, status='CANCELED',
                              exchange_order_id='2', executed_qty=D(0))

    def positions(self, symbol=None):
        return 'read'


def ref(n=0, route='classic'):
    from newcore.ports.keys import client_id_for
    return P.OrderRef(symbol='SOLUSDT', client_id=client_id_for('int_' + f'{n:032x}', route), route=route)


def mkt(qty='1', reduce=False, n=0):
    return P.MarketOrder(ref=ref(n), position_side='LONG', qty=D(qty), reduce=reduce)


def stp(n=1):
    return P.StopOrder(ref=ref(n), position_side='LONG', qty=D('1'), stop_price=D('99'))


def test_bound_counts_orders_and_ledgers_before_the_send():
    inner = Inner()
    b = BoundedPort(inner, max_orders=2, max_notional='1000', price_of={'SOLUSDT': D('100')}.get)
    b.submit_market(mkt('5'))
    b.submit_stop(stp())
    assert [x['kind'] for x in b.ledger] == ['entry', 'stop'] and b.submits == 2
    with pytest.raises(BoundExceeded, match='more than 2'):
        b.submit_market(mkt('1', reduce=True, n=2))
    assert len(inner.calls) == 2 and len(b.ledger) == 2                       # refused: never sent, never ledgered
    assert b.positions() == 'read'                                            # reads pass through


def test_bound_refuses_opening_notional_and_unknown_price_but_not_closes():
    inner = Inner()
    b = BoundedPort(inner, max_orders=5, max_notional='1000', price_of={'SOLUSDT': D('100')}.get)
    with pytest.raises(BoundExceeded, match='notional'):
        b.submit_market(mkt('10.01'))
    b.submit_market(mkt('10'))                                                # exactly at the cap
    b.submit_market(mkt('50', reduce=True, n=3))                              # a close is never notional-capped
    nb = BoundedPort(Inner(), max_orders=5, max_notional='1000', price_of={}.get)
    with pytest.raises(BoundExceeded, match='no reference price'):
        nb.submit_market(mkt('1'))


def test_bound_ledger_records_the_client_id_even_when_the_send_raises():
    class Boom(Inner):
        def submit_market(self, order):
            raise RuntimeError('wire')
    b = BoundedPort(Boom(), max_orders=5, max_notional='1000', price_of={'SOLUSDT': D('100')}.get)
    with pytest.raises(RuntimeError):
        b.submit_market(mkt('1'))
    assert b.ledger[0]['client_id'] == ref(0).client_id


def test_port_faults_apply_once_to_the_matching_effect():
    inner = Inner()
    f = PortFaults(inner)
    f.arm('close', 'refuse', -2022)
    assert f.submit_market(mkt()).kind is P.OutcomeKind.FINAL                 # an entry: not the armed effect
    out = f.submit_market(mkt(reduce=True, n=1))
    assert (out.kind, out.error_code) == (P.OutcomeKind.REJECTED, -2022) and len(inner.calls) == 1
    assert f.submit_market(mkt(reduce=True, n=2)).kind is P.OutcomeKind.FINAL  # once only
    f.arm('entry', 'lost_response')
    out = f.submit_market(mkt(n=4))
    assert out.kind is P.OutcomeKind.UNKNOWN and inner.calls[-1] == ('market', ref(4).client_id)   # executed
    f.arm('stop', 'timeout')
    n = len(inner.calls)
    assert f.submit_stop(stp(5)).kind is P.OutcomeKind.UNKNOWN and len(inner.calls) == n             # not sent
    f.arm('cancel', 'lost_response')
    assert f.cancel(ref(5)).kind is P.OutcomeKind.UNKNOWN and inner.calls[-1][0] == 'cancel'
    assert [x[:2] for x in f.injected] == [('close', 'refuse'), ('entry', 'lost_response'), ('stop', 'timeout'),
                                           ('cancel', 'lost_response')]


@pytest.mark.parametrize('on,kind,code', [('x', 'timeout', None), ('entry', 'reset', None), ('entry', 'refuse', None),
                                          ('entry', 'timeout', -1)])
def test_bad_faults_are_refused(on, kind, code):
    with pytest.raises(ValueError):
        PortFaults(Inner()).arm(on, kind, code)
    with pytest.raises(ValueError):
        HttpFaults(lambda r: None).arm(on, kind, code)


def req(method, path, query=''):
    return SimpleNamespace(method=method, url='https://testnet.binancefuture.com' + path, query=query)


@pytest.mark.parametrize('method,path,query,want', [
    ('POST', '/fapi/v1/order', 'side=BUY&positionSide=LONG&type=MARKET', 'entry'),
    ('POST', '/fapi/v1/order', 'side=SELL&positionSide=SHORT&type=MARKET', 'entry'),
    ('POST', '/fapi/v1/order', 'side=SELL&positionSide=LONG&type=MARKET', 'close'),
    ('POST', '/fapi/v1/order', 'side=BUY&positionSide=SHORT&type=MARKET', 'close'),
    ('POST', '/fapi/v1/order', 'side=SELL&positionSide=LONG&type=STOP_MARKET', 'stop'),
    ('POST', '/fapi/v1/algoOrder', 'side=SELL&positionSide=LONG', 'stop'),
    ('DELETE', '/fapi/v1/order', 'origClientOrderId=x', 'cancel'),
    ('DELETE', '/fapi/v1/algoOrder', 'clientAlgoId=x', 'cancel'),
    ('GET', '/fapi/v1/order', 'origClientOrderId=x', None),
    ('POST', '/fapi/v1/order', 'side=BUY&positionSide=LONG&type=LIMIT', None),
    ('POST', '/fapi/v1/leverage', '', None),
])
def test_classify(method, path, query, want):
    assert classify(req(method, path, query)) == want


def test_http_faults_refuse_is_synthetic_and_never_sent():
    sent = []
    h = HttpFaults(lambda r: sent.append(r) or 'answer')
    h.arm('entry', 'refuse', -4164)
    assert h(req('GET', '/fapi/v2/positionRisk')) == 'answer'                  # reads pass while armed
    out = h(req('POST', '/fapi/v1/order', 'side=BUY&positionSide=LONG&type=MARKET'))
    assert out.status == 400 and b'-4164' in out.body and len(sent) == 1
    assert h(req('POST', '/fapi/v1/order', 'side=BUY&positionSide=LONG&type=MARKET')) == 'answer'
    assert h.injected == [('entry', 'refuse', '/fapi/v1/order')]


def test_http_faults_lost_response_sends_then_times_out():
    sent = []
    h = HttpFaults(lambda r: sent.append(r) or 'answer')
    h.arm('close', 'lost_response')
    with pytest.raises(WireTimeout):
        h(req('POST', '/fapi/v1/order', 'side=SELL&positionSide=LONG&type=MARKET'))
    assert len(sent) == 1
    h.arm('stop', 'timeout')
    with pytest.raises(WireTimeout):
        h(req('POST', '/fapi/v1/algoOrder', ''))
    assert len(sent) == 1


def test_http_faults_needs_a_callable():
    with pytest.raises(ValueError):
        HttpFaults(None)
