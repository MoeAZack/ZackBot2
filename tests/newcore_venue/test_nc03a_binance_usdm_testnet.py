import hashlib
import hmac
import json
import sys
from decimal import Decimal as D
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from newcore.ports.keys import client_id_for
from newcore.ports.venue import MarketOrder, OrderRef, OutcomeKind, ReadKind, StopOrder, VenuePort
from newcore.store import create_journal, recover_journal
from newcore.venue.binance_usdm_testnet import (
    TESTNET_BASE_URL,
    BinanceUsdMTestnetVenue,
    HttpResponse,
    VenueBindingError,
)

sys.path.insert(0, str(Path(__file__).parents[1] / 'newcore_store'))
from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, INTENTS, SCENARIO  # noqa: E402

NOW = 1_799_000_000_000
INTENT = 'int_' + '12' * 16
CID = client_id_for(INTENT)
ALGO_CID = client_id_for(INTENT, 'algo')
REF = OrderRef(symbol='BTCUSDT', client_id=CID)
MARKET = MarketOrder(ref=REF, position_side='LONG', qty=D('0.01'), reduce=False)


class Credentials:
    def api_key(self):
        return 'test-key'

    def sign(self, payload):
        return hmac.new(b'test-secret', payload, hashlib.sha256).hexdigest()


class Sender:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def response(doc, status=200):
    return HttpResponse(status, json.dumps(doc, separators=(',', ':')).encode())


def venue(sender):
    return BinanceUsdMTestnetVenue(environment='testnet', base_url=TESTNET_BASE_URL, sender=sender,
                                   credentials=Credentials(), clock=lambda: NOW)


def record(*, status='NEW', cid=CID, symbol='BTCUSDT', executed='0', avg='0'):
    return dict(symbol=symbol, clientOrderId=cid, orderId=123, status=status, executedQty=executed, avgPrice=avg)


@pytest.mark.parametrize('environment,base_url', [
    ('mainnet', TESTNET_BASE_URL), ('TESTNET', TESTNET_BASE_URL), ('testnet', 'https://fapi.binance.com'),
    ('testnet', TESTNET_BASE_URL + '/'), ('testnet', 'http://testnet.binancefuture.com'),
])
def test_exact_testnet_binding_only(environment, base_url):
    with pytest.raises(VenueBindingError):
        BinanceUsdMTestnetVenue(environment=environment, base_url=base_url, sender=lambda r: None,
                                credentials=Credentials(), clock=lambda: NOW)


def test_implements_frozen_port_and_preserves_cid_with_one_request():
    sender = Sender(response(record()))
    v = venue(sender)
    assert isinstance(v, VenuePort)
    out = v.submit_market(MARKET)
    assert out.kind is OutcomeKind.KNOWN and out.executed_qty is None
    assert len(sender.requests) == 1
    req = sender.requests[0]
    assert req.url == TESTNET_BASE_URL + '/fapi/v1/order' and req.method == 'POST'
    assert parse_qs(req.query)['newClientOrderId'] == [CID]


def test_acknowledgement_has_no_execution_or_invented_exchange_record():
    out = venue(Sender(response({'symbol': 'BTCUSDT', 'clientOrderId': CID, 'orderId': 123}))).submit_market(MARKET)
    assert out.kind is OutcomeKind.ACKNOWLEDGED
    assert out.exchange_order_id is None and out.executed_qty is None


@pytest.mark.parametrize('answer,detail', [
    (TimeoutError(), 'timeout'), (OSError(), 'timeout'),
    (HttpResponse(503, b'{}'), 'http_5xx'), (HttpResponse(200, b'{'), 'malformed'),
])
def test_ambiguous_transport_is_unknown_without_retry(answer, detail):
    sender = Sender(answer)
    out = venue(sender).submit_market(MARKET)
    assert out.kind is OutcomeKind.UNKNOWN and out.detail == detail and out.executed_qty is None
    assert len(sender.requests) == 1


@pytest.mark.parametrize('doc,detail', [
    (record(cid=ALGO_CID), 'echo_mismatch'), (record(symbol='ETHUSDT'), 'echo_mismatch'),
    (record(status='MYSTERY'), 'unknown_status'), (record(status='FILLED', executed='1', avg='0'), 'final_without_price'),
])
def test_untrusted_order_record_is_unknown(doc, detail):
    out = venue(Sender(response(doc))).submit_market(MARKET)
    assert out.kind is OutcomeKind.UNKNOWN and out.detail == detail and out.executed_qty is None


@pytest.mark.parametrize('status,executed,avg,kind', [
    ('NEW', '0', '0', OutcomeKind.KNOWN),
    ('PARTIALLY_FILLED', '0.003', '60000', OutcomeKind.KNOWN),
    ('FILLED', '0.01', '60000', OutcomeKind.FINAL),
    ('CANCELED', '0', '0', OutcomeKind.FINAL),
])
def test_only_final_carries_execution(status, executed, avg, kind):
    out = venue(Sender(response(record(status=status, executed=executed, avg=avg)))).submit_market(MARKET)
    assert out.kind is kind
    assert (out.executed_qty is not None) is (kind is OutcomeKind.FINAL)
    if kind is OutcomeKind.FINAL:
        assert out.executed_qty == D(executed)


@pytest.mark.parametrize('code,kind,detail', [
    (-2013, OutcomeKind.NOT_FOUND, 'venue_not_found'),
    (-2011, OutcomeKind.NOT_FOUND, 'venue_not_found'),
    (-1111, OutcomeKind.REJECTED, 'venue_rejected'),
    (-4120, OutcomeKind.REJECTED, 'algo_route'),
    (-1007, OutcomeKind.UNKNOWN, 'ambiguous_code'),
    (-4116, OutcomeKind.UNKNOWN, 'duplicate_cid'),
])
def test_error_normalization(code, kind, detail):
    out = venue(Sender(response({'code': code, 'msg': 'x'}, 400))).query(REF)
    assert out.kind is kind and out.detail == detail and out.executed_qty is None
    if kind in (OutcomeKind.REJECTED, OutcomeKind.NOT_FOUND):
        assert out.error_code == code


def test_hostile_valid_json_integer_limit_is_unknown_malformed():
    body = b'{"code":-' + b'9' * 5000 + b',"msg":"hostile"}'
    sender = Sender(HttpResponse(400, body))
    out = venue(sender).query(REF)
    assert out.kind is OutcomeKind.UNKNOWN and out.detail == 'malformed'
    assert out.error_code is None and out.executed_qty is None
    assert len(sender.requests) == 1


@pytest.mark.parametrize('code', [0, 1, True, False, '-2013', '0'])
@pytest.mark.parametrize('status', [200, 400])
def test_nonnegative_or_noninteger_venue_code_is_unknown_malformed(code, status):
    out = venue(Sender(response({'code': code, 'msg': 'not a typed venue error'}, status))).query(REF)
    assert out.kind is OutcomeKind.UNKNOWN and out.detail == 'malformed'
    assert out.error_code is None and out.executed_qty is None


def test_same_instance_duplicate_cid_fails_closed_without_second_send():
    sender = Sender(response(record()))
    v = venue(sender)
    assert v.submit_market(MARKET).kind is OutcomeKind.KNOWN
    out = v.submit_market(MARKET)
    assert out.kind is OutcomeKind.UNKNOWN and out.detail == 'duplicate_cid'
    assert len(sender.requests) == 1


def test_duplicate_json_keys_are_malformed_not_last_value_wins():
    body = (b'{"symbol":"BTCUSDT","clientOrderId":"' + CID.encode() +
            b'","clientOrderId":"' + ALGO_CID.encode() +
            b'","orderId":123,"status":"NEW"}')
    out = venue(Sender(HttpResponse(200, body))).submit_market(MARKET)
    assert out.kind is OutcomeKind.UNKNOWN and out.detail == 'malformed'


def test_algo_stop_uses_exact_route_and_does_not_fall_back():
    ref = OrderRef(symbol='BTCUSDT', client_id=ALGO_CID, route='algo')
    stop = StopOrder(ref=ref, position_side='LONG', qty=D('0.01'), stop_price=D('58000'))
    sender = Sender(response(dict(symbol='BTCUSDT', clientAlgoId=ALGO_CID, algoId=88,
                                  algoStatus='NEW', executedQty='0')))
    out = venue(sender).submit_stop(stop)
    assert out.kind is OutcomeKind.KNOWN and len(sender.requests) == 1
    assert sender.requests[0].url.endswith('/fapi/v1/algoOrder')
    assert parse_qs(sender.requests[0].query)['clientAlgoId'] == [ALGO_CID]


@pytest.mark.parametrize('status,child,kind,exchange_id', [
    ('WORKING', None, OutcomeKind.KNOWN, '88'),
    ('TRIGGERED', 999, OutcomeKind.KNOWN, '999'),
    ('CANCELED', None, OutcomeKind.FINAL, '88'),
])
def test_algo_statuses_never_invent_execution(status, child, kind, exchange_id):
    ref = OrderRef(symbol='BTCUSDT', client_id=ALGO_CID, route='algo')
    doc = dict(symbol='BTCUSDT', clientAlgoId=ALGO_CID, algoId=88, algoStatus=status)
    if child is not None:
        doc['actualOrderId'] = child
    out = venue(Sender(response(doc))).query(ref)
    assert out.kind is kind and out.exchange_order_id == exchange_id
    assert (out.executed_qty is not None) is (kind is OutcomeKind.FINAL)
    if kind is OutcomeKind.FINAL:
        assert out.executed_qty == 0


def test_reads_are_typed_and_unknown_is_never_empty():
    sender = Sender(response([{'symbol': 'BTCUSDT', 'positionSide': 'LONG', 'positionAmt': '0.01',
                               'entryPrice': '60000'}]))
    out = venue(sender).positions('BTCUSDT')
    assert out.kind is ReadKind.OK and out.value[0].qty == D('0.01')

    bad = venue(Sender(HttpResponse(200, b'{'))).positions('BTCUSDT')
    assert bad.kind is ReadKind.UNKNOWN and bad.value is None


def test_combined_open_order_book_fails_closed_without_partial_request():
    sender = Sender()
    out = venue(sender).open_orders('BTCUSDT')
    assert out.kind is ReadKind.UNKNOWN and out.value is None and out.detail == 'split_route_read'
    assert sender.requests == []


def test_duplicate_fill_row_fails_closed():
    row = dict(id=7, orderId=123, symbol='BTCUSDT', positionSide='LONG', qty='0.01', price='60000',
               commission='0.1', commissionAsset='USDT', realizedPnl='0', maker=False, time=NOW)
    out = venue(Sender(response([row, row]))).fills('BTCUSDT', '123')
    assert out.kind is ReadKind.UNKNOWN and out.value is None and out.detail == 'duplicate_cid'


def test_file_journal_crash_restart_same_cid_queries_without_second_send(tmp_path):
    """Durable SENT is the restart authority: UNKNOWN_NEEDS_QUERY never submits the same CID again."""
    account_dir = str(tmp_path / ACCOUNT_ID)
    journal = create_journal(account_dir, ACCOUNT_ID, AGGREGATE_ID)
    for event in SCENARIO[:3]:  # decision, write-ahead intent, SENT -- crash immediately after the venue call
        journal.append(event)
    entry = INTENTS['entry']
    ref = OrderRef(symbol=entry.symbol, client_id=entry.client_order_id)
    order = MarketOrder(ref=ref, position_side=entry.side.value, qty=entry.qty, reduce=False)
    first = Sender(TimeoutError())
    assert venue(first).submit_market(order).kind is OutcomeKind.UNKNOWN
    journal.close()

    recovered = recover_journal(account_dir, ACCOUNT_ID, AGGREGATE_ID)
    assert recovered.journal.state().needs_venue_query == (entry.intent_id,)

    answer = record(cid=entry.client_order_id, symbol=entry.symbol)
    second = Sender(response(answer))
    out = venue(second).query(ref)  # runner recovery action: QUERY, not SUBMIT
    assert out.kind is OutcomeKind.KNOWN
    assert len(first.requests) == len(second.requests) == 1
    assert first.requests[0].method == 'POST' and second.requests[0].method == 'GET'
    assert parse_qs(first.requests[0].query)['newClientOrderId'] == [entry.client_order_id]
    assert parse_qs(second.requests[0].query)['origClientOrderId'] == [entry.client_order_id]
    recovered.journal.close()
