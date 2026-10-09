"""TestnetVenue.order_by_id (S1 VenuePort, Codex 6071659449): one order by its EXCHANGE order id over
GET /fapi/v1/order?orderId= (classic, and the child of a triggered algo stop) and, only after a classic -2013,
GET /fapi/v1/algoOrder?algoId=. Fake HTTP + hand-written fixtures; dummy credentials; no network."""
import json
import logging
from decimal import Decimal as D

import pytest

from newcore.ports import keys as K
from newcore.ports import venue as P
from newcore.ports.values import PortValueError
from newcore.venue.credentials import StaticCredentials
from newcore.venue.testnet_venue import TestnetVenue
from newcore.venue.transport import BinanceTestnetTransport, PositionMode
from newcore.venue.wire import WireTimeout

from ncv_support import DUMMY_KEY, DUMMY_SECRET, NOW_MS, FakeHttp, fixture, query_pairs, raw

INTENT = 'int_' + '6' * 32
CID = K.client_id_for(INTENT, 'classic')
ACID = K.client_id_for(INTENT, 'algo')
SYM = 'SOLUSDT'
OID = '4000000100'                     # orderId of order_query_filled
AID = '2146760'                        # algoId of the algo fixtures
OBS = 1759917601234


def venue(*script):
    http = FakeHttp(*script)
    t = BinanceTestnetTransport(environment='testnet', http=http, clock=lambda: NOW_MS,
                                position_mode=PositionMode.HEDGE,
                                credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
    return TestnetVenue(t, lambda: OBS), http


def body(name, drop=(), **patch):
    b = json.loads(fixture(name).body)
    b.update(patch)
    for k in drop:
        b.pop(k)
    return raw(fixture(name).status, json.dumps(b).encode(), dict(fixture(name).headers))


def text(status, s):
    return raw(status, s.encode())


def classic(drop=(), **patch):
    return body('order_query_filled', drop, **{'clientOrderId': CID, **patch})


def unknown(out, detail=None):
    assert out.kind is P.ReadKind.UNKNOWN and out.value is None and out.error_code is None
    if detail is not None:
        assert out.detail == detail, out.detail


# ---------- OK ----------

def test_ok_classic_one_request_by_order_id():
    v, http = venue(classic(status='PARTIALLY_FILLED', origQty='2', executedQty='1'))
    out = v.order_by_id(SYM, OID)
    assert out.kind is P.ReadKind.OK and out.observed_at_ms == OBS
    rec, = out.value
    assert isinstance(rec, P.VenueOrderRecord)
    assert (rec.ref, rec.exchange_order_id, rec.position_side, rec.status, rec.orig_qty, rec.executed_qty) == (
        P.OrderRef(symbol=SYM, client_id=CID), OID, 'LONG', 'PARTIALLY_FILLED', D('2'), D('1'))
    assert len(http.requests) == 1 and http.last.method == 'GET' and http.last.url.endswith('/fapi/v1/order')
    q = dict(query_pairs(http.last))
    assert (q['symbol'], q['orderId']) == (SYM, OID) and 'origClientOrderId' not in q and 'signature' in q


def test_ok_classic_child_of_a_newcore_algo_stop_keeps_the_algo_route():
    v, _ = venue(classic(clientOrderId=ACID))
    rec, = v.order_by_id(SYM, OID).value
    assert rec.ref == P.OrderRef(symbol=SYM, client_id=ACID, route='algo')


def test_ok_foreign_client_id_is_reported_verbatim():
    v, _ = venue(classic(clientOrderId='web_manual_close_1'))
    rec, = v.order_by_id(SYM, OID).value
    assert rec.ref.client_id == 'web_manual_close_1' and rec.ref.route == 'classic'


def test_ok_algo_by_algo_id_after_classic_not_found():
    v, http = venue('err_no_such_order', body('algo_order_new', clientAlgoId=ACID, algoStatus='CANCELED'))
    out = v.order_by_id(SYM, AID)
    assert out.kind is P.ReadKind.OK
    rec, = out.value
    assert (rec.ref, rec.exchange_order_id, rec.status, rec.orig_qty, rec.executed_qty) == (
        P.OrderRef(symbol=SYM, client_id=ACID, route='algo'), AID, 'CANCELED', D('10'), D('0'))
    assert len(http.requests) == 2 and http.last.url.endswith('/fapi/v1/algoOrder')
    q = dict(query_pairs(http.last))
    assert q['algoId'] == AID and 'clientAlgoId' not in q


@pytest.mark.parametrize('name', ['algo_order_triggered', 'algo_order_finished'])
def test_triggered_algo_is_unknown_its_fill_lives_in_the_child(name):
    v, _ = venue('err_no_such_order', body(name, clientAlgoId=ACID))
    unknown(v.order_by_id(SYM, AID), 'algo_triggered')


# ---------- NOT_FOUND (REJECTED -2013) ----------

def test_not_found_on_both_books():
    v, http = venue('err_no_such_order', 'err_no_such_order')
    out = v.order_by_id(SYM, '999999')
    assert out.kind is P.ReadKind.REJECTED and out.error_code == -2013 and out.value is None
    assert [r.url.rsplit('/', 1)[1] for r in http.requests] == ['order', 'algoOrder']


def test_not_found_algo_message_variant():
    v, _ = venue('err_no_such_order', 'err_algo_not_exist_msg')
    out = v.order_by_id(SYM, '999999')
    assert out.kind is P.ReadKind.REJECTED and out.error_code == -2013


def test_other_classic_refusal_is_passed_through_without_an_algo_read():
    v, http = venue('err_signature')
    out = v.order_by_id(SYM, OID)
    assert out.kind is P.ReadKind.REJECTED and out.error_code == -1022 and len(http.requests) == 1


# ---------- UNKNOWN ----------

@pytest.mark.parametrize('answer,detail', [
    (WireTimeout(), 'timeout'), ('err_502_html', 'http_5xx'), ('err_busy_503', 'http_5xx'),
    ('ok_non_json', 'unreadable_body'), (text(200, '{"orderId": 4000000100,'), 'unreadable_body'),
    ('err_backend_timeout', 'ambiguous_code')])
def test_no_answer_is_unknown_never_not_found(answer, detail):
    v, http = venue(answer)
    unknown(v.order_by_id(SYM, OID), detail)
    assert len(http.requests) == 1


@pytest.mark.parametrize('field', ['origQty', 'executedQty', 'clientOrderId', 'orderId', 'symbol', 'positionSide',
                                   'status'])
def test_missing_field_is_unknown(field):
    v, _ = venue(classic(drop=(field,)))
    unknown(v.order_by_id(SYM, OID), 'malformed')


def test_wrong_symbol_echo_is_unknown():
    v, _ = venue(classic(symbol='BTCUSDT'))
    unknown(v.order_by_id(SYM, OID), 'echo_mismatch')


def test_wrong_order_id_echo_is_unknown():
    v, _ = venue(classic(orderId=4000000101))
    unknown(v.order_by_id(SYM, OID), 'echo_mismatch')


def test_wrong_algo_id_or_symbol_echo_is_unknown():
    v, _ = venue('err_no_such_order', body('algo_order_new', clientAlgoId=ACID, algoId=2146761))
    unknown(v.order_by_id(SYM, AID), 'echo_mismatch')
    v, _ = venue('err_no_such_order', body('algo_order_new', clientAlgoId=ACID, symbol='BTCUSDT'))
    unknown(v.order_by_id(SYM, AID), 'echo_mismatch')


@pytest.mark.parametrize('qty_json', ['0.1', '1e1', '10'])
def test_float_looking_quantity_is_unknown(qty_json):
    """origQty sent as a JSON number (not "0.1"): never trusted, even when it parses to an exact Decimal."""
    s = json.loads(fixture('order_query_filled').body)
    s.update(clientOrderId=CID, executedQty='0', status='NEW')
    s.pop('origQty')
    payload = json.dumps(s)[:-1] + ', "origQty": ' + qty_json + '}'
    v, _ = venue(text(200, payload))
    unknown(v.order_by_id(SYM, OID), 'malformed')
    algo = json.dumps(dict(json.loads(fixture('algo_order_new').body), clientAlgoId=ACID))
    v, _ = venue('err_no_such_order', text(200, algo.replace('"quantity": "10"', '"quantity": ' + qty_json)))
    unknown(v.order_by_id(SYM, AID), 'malformed')


def test_algo_book_unreadable_after_classic_not_found_is_unknown():
    for answer, detail in ((WireTimeout(), 'timeout'), ('err_502_html', 'http_5xx'),
                           ('err_path_invalid', None), ('err_404_html', None)):
        v, _ = venue('err_no_such_order', answer)
        out = v.order_by_id(SYM, AID)
        unknown(out, detail)


def test_one_way_order_is_unknown():
    v, _ = venue(classic(positionSide='BOTH'))
    unknown(v.order_by_id(SYM, OID), 'unrepresentable')


def test_executed_above_original_is_unknown():
    v, _ = venue(classic(status='PARTIALLY_FILLED', origQty='1', executedQty='2'))
    unknown(v.order_by_id(SYM, OID), 'malformed')


# ---------- arguments: refused before anything is sent ----------

@pytest.mark.parametrize('sym,eoid', [('solusdt', OID), (SYM, ''), (SYM, '0'), (SYM, '01'), (SYM, '-5'), (SYM, '1.5'),
                                      (SYM, 4000000100), (SYM, '١٢٣'), (SYM, '9' * 20)])
def test_bad_arguments_raise_before_sending(sym, eoid):
    v, http = venue()
    with pytest.raises(PortValueError):
        v.order_by_id(sym, eoid)
    assert http.requests == []


# ---------- no secret in any result or log ----------

def test_no_secret_in_results_or_logs(caplog):
    leaky = raw(400, json.dumps({'code': -1022, 'msg': 'bad sig for key ' + DUMMY_KEY + ' / ' + DUMMY_SECRET}).encode())
    leaky5 = raw(503, json.dumps({'code': -1008, 'msg': 'busy ' + DUMMY_KEY}).encode())
    scripts = [(classic(),), ('err_no_such_order', body('algo_order_new', clientAlgoId=ACID)),
               ('err_no_such_order', 'err_no_such_order'), (WireTimeout(),), (leaky,), (leaky5,),
               ('err_no_such_order', leaky), (classic(symbol='BTCUSDT'),), (text(200, '{"x"'),)]
    seen = []
    with caplog.at_level(logging.DEBUG):
        for script in scripts:
            v, _ = venue(*script)
            out = v.order_by_id(SYM, OID)
            seen.append(repr(out) + str(out.detail) + repr(v))
    blob = '\n'.join(seen) + caplog.text
    assert len(seen) == len(scripts)
    for s in (DUMMY_KEY, DUMMY_SECRET, DUMMY_KEY[:24], DUMMY_SECRET[:24]):
        assert s not in blob
