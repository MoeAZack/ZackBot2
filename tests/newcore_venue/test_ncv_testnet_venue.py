"""TestnetVenue: the frozen newcore.ports.VenuePort over the testnet transport. Fake HTTP + hand-written fixtures +
cassette replay only; dummy credentials; no network."""
import json
from decimal import Decimal as D

import pytest

from newcore.ports import keys as K
from newcore.ports import venue as P
from newcore.ports.values import PortValueError
from newcore.venue.cassette import CassettePlayer, CassetteRecorder
from newcore.venue.credentials import StaticCredentials
from newcore.venue.errors import NotFoundEvidenceType
from newcore.venue.outcomes import OrderOutcome as TOut, OrderOutcomeKind as TK
from newcore.venue.testnet_venue import (HedgeModeRequired, TestnetAccountReader, TestnetVenue, VenueBootUnknown,
                                         map_order_outcome)
from newcore.venue.transport import BinanceTestnetTransport, PositionMode
from newcore.venue.wire import WireTimeout

from ncv_support import DUMMY_KEY, DUMMY_SECRET, NOW_MS, FakeHttp, fixture, query_pairs, raw

INTENT = 'int_' + '7' * 32
CID = K.client_id_for(INTENT, 'classic')
ACID = K.client_id_for(INTENT, 'algo')
REF = P.OrderRef(symbol='SOLUSDT', client_id=CID)
AREF = P.OrderRef(symbol='SOLUSDT', client_id=ACID, route='algo')
OBS = 1759917601234


def venue(*script, mode=PositionMode.HEDGE):
    http = FakeHttp(*script)
    t = BinanceTestnetTransport(environment='testnet', http=http, clock=lambda: NOW_MS, position_mode=mode,
                                credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
    return TestnetVenue(t, lambda: OBS), http


def body_with(name, **patch):
    b = json.loads(fixture(name).body)
    b.update(patch)
    return raw(fixture(name).status, json.dumps(b).encode(), dict(fixture(name).headers))


def market(reduce=False, ps='LONG', qty='10'):
    return P.MarketOrder(ref=REF, position_side=ps, qty=D(qty), reduce=reduce)


# ---------- the port contract ----------

def test_conforms_to_the_frozen_port():
    v, _ = venue()
    assert isinstance(v, P.VenuePort)


def test_submit_market_final_carries_executed_and_price():
    v, http = venue(body_with('order_market_filled', clientOrderId=CID))
    out = v.submit_market(market())
    assert isinstance(out, P.OrderOutcome) and out.kind is P.OutcomeKind.FINAL and out.ref == REF
    assert (out.status, out.exchange_order_id, out.executed_qty, out.avg_price) == ('FILLED', '4000000100', D('10'),
                                                                                  D('222.3'))
    assert out.observed_at_ms == OBS and len(http.requests) == 1
    q = dict(query_pairs(http.last))
    assert (q['side'], q['positionSide'], q['newClientOrderId'], q['type']) == ('BUY', 'LONG', CID, 'MARKET')
    assert 'reduceOnly' not in q


def test_submit_market_reduce_short_is_buy_on_short_side():
    v, http = venue(body_with('order_market_filled', clientOrderId=CID, side='BUY', positionSide='SHORT'))
    v.submit_market(market(reduce=True, ps='SHORT'))
    q = dict(query_pairs(http.last))
    assert (q['side'], q['positionSide']) == ('BUY', 'SHORT') and 'reduceOnly' not in q


def test_known_carries_no_executed_qty():
    v, _ = venue(body_with('order_market_new', clientOrderId=CID))
    out = v.submit_market(market())
    assert out.kind is P.OutcomeKind.KNOWN and out.executed_qty is None and out.status == 'NEW'


def test_final_expired_partial_keeps_executed():
    v, _ = venue(body_with('order_market_expired_partial', clientOrderId=CID))
    out = v.submit_market(market())
    assert out.kind is P.OutcomeKind.FINAL and out.executed_qty == D('4') and out.avg_price == D('222.3')


def test_final_canceled_zero_executed_has_no_price():
    v, _ = venue(body_with('order_cancel_canceled', clientOrderId=CID))
    out = v.cancel(REF)
    assert out.kind is P.OutcomeKind.FINAL and out.executed_qty == D('0') and out.avg_price is None


@pytest.mark.parametrize('answer,detail', [(WireTimeout(), 'timeout'), ('err_502_html', 'http_5xx'),
                                           ('err_backend_timeout', 'ambiguous_code')])
def test_unknown_is_never_failed_and_never_executed(answer, detail):
    v, http = venue(answer)
    out = v.submit_market(market())
    assert out.kind is P.OutcomeKind.UNKNOWN and out.detail == detail and out.executed_qty is None
    assert len(http.requests) == 1


def test_rejected_carries_the_code_and_one_request_only():
    v, http = venue('err_margin')
    out = v.submit_market(market())
    assert out.kind is P.OutcomeKind.REJECTED and out.error_code == -2019 and out.detail == 'insufficient_margin'
    assert len(http.requests) == 1


def test_classic_stop_algo_hint_is_a_rejection_not_a_fallback():
    v, http = venue('err_algo_switch')
    out = v.submit_stop(P.StopOrder(ref=REF, position_side='LONG', qty=D('10'), stop_price=D('211.4')))
    assert out.kind is P.OutcomeKind.REJECTED and out.error_code == -4120 and out.detail == 'algo_route'
    assert len(http.requests) == 1 and http.last.url.endswith('/fapi/v1/order')      # no hidden second send


def test_algo_stop_uses_the_algo_client_id():
    v, http = venue(body_with('algo_order_new', clientAlgoId=ACID))
    out = v.submit_stop(P.StopOrder(ref=AREF, position_side='LONG', qty=D('10'), stop_price=D('211.4')))
    assert out.kind is P.OutcomeKind.KNOWN and out.exchange_order_id == '2146760'
    q = dict(query_pairs(http.last))
    assert http.last.url.endswith('/fapi/v1/algoOrder') and q['clientAlgoId'] == ACID and q['side'] == 'SELL'


def test_not_found_keeps_its_evidence_and_books_nothing():
    v, _ = venue('err_no_such_order', 'err_cancel_unknown')
    q = v.query(REF)
    assert q.kind is P.OutcomeKind.NOT_FOUND and q.error_code == -2013 and q.executed_qty is None
    assert q.detail == NotFoundEvidenceType.QUERY_NO_SUCH_ORDER.value
    c = v.cancel(REF)
    assert c.kind is P.OutcomeKind.NOT_FOUND and c.error_code == -2011
    assert c.detail == NotFoundEvidenceType.CANCEL_UNKNOWN_ORDER.value


@pytest.mark.parametrize('body', [b'{"code": -4116, "msg": "ClientOrderId is duplicated."}',
                                  b'{"code": -3999, "msg": "Duplicate clientOrderId sent."}'])
def test_duplicate_client_id_means_the_order_exists(body):
    v, http = venue(raw(400, body))
    out = v.submit_market(market())
    assert out.kind is P.OutcomeKind.UNKNOWN and out.detail == 'duplicate_client_id' and out.executed_qty is None
    assert out.kind is not P.OutcomeKind.REJECTED and len(http.requests) == 1


def test_duplicate_client_id_on_algo_stop_too():
    v, _ = venue(raw(400, b'{"code": -4116, "msg": "ClientOrderId is duplicated."}'))
    out = v.submit_stop(P.StopOrder(ref=AREF, position_side='LONG', qty=D('1'), stop_price=D('2')))
    assert out.kind is P.OutcomeKind.UNKNOWN and out.detail == 'duplicate_client_id'


def test_algo_cancel_ack_and_algo_states():
    v, _ = venue(body_with('algo_cancel_ack', clientAlgoId=ACID), body_with('algo_order_triggered', clientAlgoId=ACID),
                 body_with('algo_order_finished', clientAlgoId=ACID),
                 body_with('algo_order_finished', clientAlgoId=ACID, algoStatus='CANCELED', actualOrderId=None))
    assert v.cancel(AREF).kind is P.OutcomeKind.ACKNOWLEDGED
    trig = v.query(AREF)
    assert trig.kind is P.OutcomeKind.KNOWN and trig.exchange_order_id == '4000000222' and trig.detail == 'algo_triggered'
    fin = v.query(AREF)          # FINISHED: fill truth is the child order -> KNOWN, never an invented executed qty
    assert fin.kind is P.OutcomeKind.KNOWN and fin.executed_qty is None and fin.exchange_order_id == '4000000222'
    canceled = v.query(AREF)
    assert canceled.kind is P.OutcomeKind.FINAL and canceled.executed_qty == D('0')


def test_canceled_record_with_zero_avg_price_is_final_zero_without_price():
    # Binance sends avgPrice "0.00000" on an unfilled order; the port requires no price when nothing executed.
    v, _ = venue(body_with('order_cancel_canceled', clientOrderId=CID, avgPrice='0.00000'))
    out = v.cancel(REF)
    assert out.kind is P.OutcomeKind.FINAL and out.executed_qty == D('0') and out.avg_price is None


@pytest.mark.parametrize('status', ['TRIGGERED', 'TRIGGERING', 'FINISHED'])
def test_triggered_algo_without_child_id_is_unknown_never_final(status):
    v, _ = venue(body_with('algo_order_finished', clientAlgoId=ACID, algoStatus=status, actualOrderId=None))
    out = v.query(AREF)
    assert out.kind is P.OutcomeKind.UNKNOWN and out.detail == 'algo_triggered_no_child' and out.executed_qty is None


def test_unrepresentable_answer_is_unknown_not_an_exception():
    long_status = body_with('order_market_filled', clientOrderId=CID)
    v, _ = venue(long_status)
    import newcore.venue.testnet_venue as tv
    orig = tv.map_order_outcome

    def boom(*a, **k):
        raise PortValueError('x', 'unrepresentable')
    tv.map_order_outcome = boom
    try:
        out = v.submit_market(market())
    finally:
        tv.map_order_outcome = orig
    assert out.kind is P.OutcomeKind.UNKNOWN and out.detail == 'unrepresentable'


# ---------- client ids ----------

def test_submit_requires_a_newcore_client_id_of_its_route():
    v, http = venue()
    wrong_route = P.OrderRef(symbol='SOLUSDT', client_id=ACID, route='classic')
    foreign = P.OrderRef(symbol='SOLUSDT', client_id='web_foreign1')
    for call in (lambda: v.submit_market(P.MarketOrder(ref=wrong_route, position_side='LONG', qty=D('1'),
                                                       reduce=False)),
                 lambda: v.submit_market(P.MarketOrder(ref=foreign, position_side='LONG', qty=D('1'), reduce=False)),
                 lambda: v.submit_stop(P.StopOrder(ref=P.OrderRef(symbol='SOLUSDT', client_id=CID, route='algo'),
                                                   position_side='LONG', qty=D('1'), stop_price=D('2'))),
                 lambda: v.cancel(foreign)):
        with pytest.raises(PortValueError):
            call()
    assert http.requests == []


def test_query_of_a_foreign_order_is_allowed():
    v, _ = venue('err_no_such_order')
    assert v.query(P.OrderRef(symbol='SOLUSDT', client_id='web_foreign1')).kind is P.OutcomeKind.NOT_FOUND


# ---------- hedge mode ----------

def test_one_way_transport_refused_at_construction():
    t = BinanceTestnetTransport(environment='testnet', http=FakeHttp(), clock=lambda: NOW_MS,
                                position_mode=PositionMode.ONE_WAY)
    with pytest.raises(HedgeModeRequired):
        TestnetVenue(t, lambda: OBS)


def test_boot_check_hedge_mode():
    v, _ = venue('dual_side_true')
    v.check_hedge_mode()
    v, _ = venue(raw(200, b'{"dualSidePosition": false}'))
    with pytest.raises(HedgeModeRequired):
        v.check_hedge_mode()
    v, _ = venue(WireTimeout())
    with pytest.raises(VenueBootUnknown):
        v.check_hedge_mode()


def test_one_way_position_row_is_unknown_not_guessed():
    rows = json.loads(fixture('position_risk_hedge').body)
    rows[0]['positionSide'] = 'BOTH'
    v, _ = venue(raw(200, json.dumps(rows).encode()))
    out = v.positions()
    assert out.kind is P.ReadKind.UNKNOWN and out.value is None


# ---------- reads ----------

def test_positions_both_sides_unsigned():
    v, _ = venue('position_risk_hedge')
    out = v.positions('SOLUSDT')
    assert out.kind is P.ReadKind.OK and isinstance(out.value[0], P.VenuePosition)
    assert [(p.side, p.qty) for p in out.value] == [('LONG', D('10')), ('SHORT', D('0'))]


def test_open_orders_classic_and_algo():
    v, http = venue('open_orders', 'open_algo_orders_list')
    out = v.open_orders('SOLUSDT')
    assert out.kind is P.ReadKind.OK and len(out.value) == 2 and len(http.requests) == 2
    classic, algo = out.value
    assert classic.ref.route == 'classic' and classic.reduce and classic.stop_price == D('211.40')
    assert algo.ref.route == 'algo' and algo.ref.client_id == 'zb-es-QQQQRRRRSSSSTTTTUUUU' and algo.qty == D('10')


@pytest.mark.parametrize('algo_answer,detail', [(WireTimeout(), 'timeout'),
                                                ('err_path_invalid', 'algo_read_endpoint_unsupporte'),
                                                ('open_algo_orders_bad_shape', 'malformed')])
def test_open_orders_partial_read_is_unknown(algo_answer, detail):
    v, _ = venue('open_orders', algo_answer)
    out = v.open_orders()
    assert out.kind is P.ReadKind.UNKNOWN and out.value is None and out.detail.startswith(detail)


def test_a_full_page_of_fills_is_unknown_not_complete():
    row = json.loads(fixture('user_trades').body)[0]
    rows = [dict(row, id=1000 + i, time=1759917000045 + i) for i in range(1000)]
    v, _ = venue(raw(200, json.dumps(rows).encode()))
    out = v.fills('SOLUSDT', '4000000100')
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'fills_truncated'


def test_open_orders_classic_rejected_is_rejected():
    v, http = venue('err_signature')
    out = v.open_orders()
    assert out.kind is P.ReadKind.REJECTED and out.error_code == -1022 and len(http.requests) == 1


def test_fills_of_one_order_oldest_first():
    trades = json.loads(fixture('user_trades').body)
    v, http = venue(raw(200, json.dumps(list(reversed(trades))).encode()))
    out = v.fills('SOLUSDT', '4000000100')
    assert out.kind is P.ReadKind.OK and [f.trade_id for f in out.value] == ['698759', '698760']
    assert out.value[0].fee == D('0.55575') and dict(query_pairs(http.last))['orderId'] == '4000000100'


def test_fills_of_another_order_are_unknown():
    v, _ = venue('user_trades')
    assert v.fills('SOLUSDT', '4000000999').kind is P.ReadKind.UNKNOWN


@pytest.mark.parametrize('oid', ['', 'abc', '0123', '-1', '1.5', '١٢'])
def test_fills_bad_order_id_refused_before_sending(oid):
    v, http = venue()
    with pytest.raises(PortValueError):
        v.fills('SOLUSDT', oid)
    assert http.requests == []


# ---------- pure mapping ----------

def test_map_order_outcome_table():
    unk = TOut(TK.UNKNOWN, 'place_market', 'classic', CID, unknown_reason='timeout')
    assert map_order_outcome(unk, REF, OBS).kind is P.OutcomeKind.UNKNOWN
    ack = TOut(TK.ACKNOWLEDGED, 'cancel_algo', 'algo', ACID)
    assert map_order_outcome(ack, AREF, OBS).kind is P.OutcomeKind.ACKNOWLEDGED


# ---------- equity / funding reader ----------

def test_account_reader_equity_and_funding():
    http = FakeHttp('account_v2', raw(200, json.dumps([r for r in json.loads(fixture('income_mixed').body)
                                                       if r['incomeType'] == 'FUNDING_FEE']).encode()))
    t = BinanceTestnetTransport(environment='testnet', http=http, clock=lambda: NOW_MS,
                                position_mode=PositionMode.HEDGE, credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
    r = TestnetAccountReader(t, lambda: OBS)
    eq = r.equity()
    assert eq.kind is P.ReadKind.OK and eq.value[0].wallet_balance == D('5000.12345678')
    fund = r.funding(start_ms=1759939000000, end_ms=1759940000000)
    assert fund.kind is P.ReadKind.OK and sum(f.amount for f in fund.value) == D('-0.06855')
    assert dict(query_pairs(http.last))['incomeType'] == 'FUNDING_FEE'


def test_account_reader_missing_asset_is_unknown_not_zero():
    t = BinanceTestnetTransport(environment='testnet', http=FakeHttp('account_v2'), clock=lambda: NOW_MS,
                                position_mode=PositionMode.HEDGE, credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
    out = TestnetAccountReader(t, lambda: OBS).equity('BTC')
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'asset_missing'


def test_not_on_the_port():
    assert not hasattr(P.VenuePort, 'equity') and not hasattr(TestnetVenue, 'equity')


# ---------- cassette replay through the adapter ----------

def test_cassette_replay_reproduces_port_outcomes():
    script = [body_with('order_market_filled', clientOrderId=CID), body_with('algo_order_new', clientAlgoId=ACID),
              'position_risk_hedge', 'open_orders', 'open_algo_orders_list', 'err_no_such_order']
    rec = CassetteRecorder(FakeHttp(*script), redact=(DUMMY_SECRET,))
    t = BinanceTestnetTransport(environment='testnet', http=rec, clock=lambda: NOW_MS,
                                position_mode=PositionMode.HEDGE, credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))

    def session(v):
        return [v.submit_market(market()),
                v.submit_stop(P.StopOrder(ref=AREF, position_side='LONG', qty=D('10'), stop_price=D('211.4'))),
                v.positions(), v.open_orders(), v.query(REF)]
    first = session(TestnetVenue(t, lambda: OBS))
    text = rec.to_json()
    assert DUMMY_KEY not in text and DUMMY_SECRET not in text
    t2 = BinanceTestnetTransport(environment='testnet', http=CassettePlayer(json.loads(text)), clock=lambda: NOW_MS + 9,
                                 position_mode=PositionMode.HEDGE,
                                 credentials=StaticCredentials('REPLAY' + 'k' * 58, 'REPLAY' + 's' * 58))
    assert session(TestnetVenue(t2, lambda: OBS)) == first
