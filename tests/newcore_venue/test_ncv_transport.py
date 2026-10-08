"""NC-03 wire layer: BinanceTestnetTransport request shapes, outcome typing and the no-retry contract."""
from decimal import Decimal as D
from urllib.parse import parse_qsl

import pytest

from newcore.venue.credentials import CredentialsUnavailable, key_digest
from newcore.venue.errors import ErrorCategory, NotFoundEvidenceType
from newcore.venue.guard import TESTNET_BASE_URL, VenueGuardError
from newcore.venue.outcomes import OrderOutcomeKind as K, ReadKind
from newcore.venue.signing import hmac_sha256_hex
from newcore.venue.transport import BinanceTestnetTransport, PositionMode, StopRoute, VenueInputError
from newcore.venue.wire import HttpResponse, WireConnectionError, WireTimeout

from ncv_support import DUMMY_KEY, DUMMY_SECRET, NOW_MS, FakeHttp, make, raw

CID = 'zb-ABCDEFGHIJKLMNOPQRSTUVWX'
SCID = 'zb-es-AAAABBBBCCCCDDDDEEEE'
ACID = 'zb-es-QQQQRRRRSSSSTTTTUUUU'


def check_signed(req, expect_pairs, *, recv=5000, now=NOW_MS):
    """The request is signed over exactly the sent query; business params first (in order), then timestamp,
    recvWindow, signature; the key header is present."""
    pairs = parse_qsl(req.query, keep_blank_values=True, strict_parsing=True)
    assert pairs[:-3] == expect_pairs
    assert pairs[-3:-1] == [('timestamp', str(now)), ('recvWindow', str(recv))]
    assert pairs[-1][0] == 'signature'
    unsigned = req.query.rsplit('&signature=', 1)[0]
    assert pairs[-1][1] == hmac_sha256_hex(DUMMY_SECRET.encode(), unsigned.encode())
    assert req.wire_header('X-MBX-APIKEY') == DUMMY_KEY and req.header('X-MBX-APIKEY') == '<redacted>' and req.signed


def check_unsigned(req, expect_pairs):
    assert parse_qsl(req.query, keep_blank_values=True) == expect_pairs
    assert req.header('X-MBX-APIKEY') is None and not req.signed and 'signature' not in req.query


# ---------- construction / guard ----------

def test_constructor_refuses_mainnet_url():
    with pytest.raises(VenueGuardError):
        BinanceTestnetTransport(environment='testnet', base_url='https://fapi.binance.com', http=FakeHttp(),
                                clock=lambda: NOW_MS, position_mode=PositionMode.HEDGE)


@pytest.mark.parametrize('env', ['mainnet', 'TESTNET', None, 'live'])
def test_constructor_refuses_mismatched_environment(env):
    with pytest.raises(VenueGuardError):
        BinanceTestnetTransport(environment=env, http=FakeHttp(), clock=lambda: NOW_MS,
                                position_mode=PositionMode.HEDGE)


def test_constructor_requires_environment_keyword():
    with pytest.raises(TypeError):
        BinanceTestnetTransport(http=FakeHttp(), clock=lambda: NOW_MS, position_mode=PositionMode.HEDGE)


def test_mutated_base_url_cannot_leave_testnet():
    t, http = make('server_time')
    object.__setattr__(t, '_base_url', 'https://fapi.binance.com')
    with pytest.raises(VenueGuardError):
        t.server_time()
    assert http.requests == []


@pytest.mark.parametrize('kw', [dict(position_mode='hedge'), dict(http=None), dict(clock=5),
                                dict(recv_window_ms=0), dict(recv_window_ms=70000), dict(recv_window_ms=60000), dict(timeout_s=0),
                                dict(timeout_s=True), dict(credentials=object())])
def test_constructor_validates(kw):
    args = dict(environment='testnet', http=FakeHttp(), clock=lambda: NOW_MS, position_mode=PositionMode.HEDGE)
    args.update(kw)
    with pytest.raises(VenueInputError):
        BinanceTestnetTransport(**args)


def test_every_request_targets_pinned_host():
    t, http = make('server_time', 'exchange_info', 'klines_4h', 'account_v2', 'position_risk_hedge')
    t.server_time(); t.exchange_info(); t.klines('SOLUSDT', '4h'); t.account(); t.positions()
    assert all(r.url.startswith(TESTNET_BASE_URL + '/fapi/') for r in http.requests)


# ---------- unsigned market data ----------

def test_server_time():
    t, http = make('server_time')
    out = t.server_time()
    assert out.kind is ReadKind.OK and out.value == 1759917600000 and out.rate.weight('1m') == 1
    assert (http.last.method, http.last.url) == ('GET', TESTNET_BASE_URL + '/fapi/v1/time')
    check_unsigned(http.last, [])


def test_exchange_info():
    t, http = make('exchange_info')
    out = t.exchange_info()
    assert out.ok and out.value.symbols['SOLUSDT'].step_size == D('1')
    assert http.last.url.endswith('/fapi/v1/exchangeInfo')
    check_unsigned(http.last, [])


def test_klines_request_and_parse():
    t, http = make('klines_4h')
    out = t.klines('SOLUSDT', '4h', start_ms=1759881600000, end_ms=1759924799999, limit=3)
    assert out.ok and len(out.value) == 3 and out.value[0].open == D('220.10')
    assert http.last.url.endswith('/fapi/v1/klines')
    check_unsigned(http.last, [('symbol', 'SOLUSDT'), ('interval', '4h'), ('startTime', '1759881600000'),
                               ('endTime', '1759924799999'), ('limit', '3')])


def test_klines_works_without_credentials():
    t, http = make('klines_4h', creds=False)
    assert t.klines('SOLUSDT', '4h').ok


@pytest.mark.parametrize('kw', [dict(symbol='solusdt'), dict(interval='7m'), dict(limit=0), dict(limit=1501),
                                dict(start_ms=-1), dict(start_ms=2, end_ms=1), dict(start_ms=1.5)])
def test_klines_input_validation_sends_nothing(kw):
    t, http = make()
    args = dict(symbol='SOLUSDT', interval='4h')
    args.update(kw)
    sym, iv = args.pop('symbol'), args.pop('interval')
    with pytest.raises(VenueInputError):
        t.klines(sym, iv, **args)
    assert http.requests == []


# ---------- symbol grammar (NC-01: [A-Z0-9]{2,30}, Codex ruling C1) ----------

@pytest.mark.parametrize('n', [2, 20, 21, 25, 29, 30])
def test_symbol_length_2_to_30_accepted(n):
    sym = ('1000' + 'X' * 40)[:n]
    t, http = make(raw(200, b'[]'), raw(200, b'{}'), raw(200, b'[]'))
    t.klines(sym, '4h')
    assert dict(parse_qsl(http.last.query))['symbol'] == sym
    t.place_market(sym, 'BUY', 'LONG', D('1'), CID, reduce_only=False)
    assert dict(parse_qsl(http.last.query))['symbol'] == sym
    t.positions(sym)
    assert dict(parse_qsl(http.last.query))['symbol'] == sym


@pytest.mark.parametrize('sym', ['A', 'A' * 31, 'A' * 40, '', 'solusdt', 'SOL-USDT', 'SOL_USDT', 'SOL USDT',
                                 'ＳＯＬＵＳＤＴ', 'SOLUSDT\n', None, 7])
def test_symbol_outside_grammar_refused_everywhere(sym):
    t, http = make()
    calls = [lambda: t.klines(sym, '4h'), lambda: t.positions(sym), lambda: t.open_orders(sym),
             lambda: t.open_algo_orders(sym), lambda: t.user_trades(sym), lambda: t.income(symbol=sym),
             lambda: t.place_market(sym, 'BUY', 'LONG', D('1'), CID, reduce_only=False),
             lambda: t.place_stop_market(sym, 'LONG', D('1'), D('2'), SCID, route=StopRoute.CLASSIC),
             lambda: t.query_order(sym, CID), lambda: t.cancel_order(sym, CID)]
    for call in calls:
        if sym is None and call in (calls[1], calls[2], calls[3], calls[5]):
            continue                                   # None = "all symbols" on these reads
        with pytest.raises(VenueInputError):
            call()
    assert http.requests == []


# ---------- signed reads ----------

def test_account():
    t, http = make('account_v2')
    out = t.account()
    assert out.ok and out.value.total_wallet_balance == D('5000.12345678')
    assert (http.last.method, http.last.url) == ('GET', TESTNET_BASE_URL + '/fapi/v2/account')
    check_signed(http.last, [])


def test_positions_all_and_symbol():
    t, http = make('position_risk_hedge', 'position_risk_hedge')
    assert t.positions().ok
    check_signed(http.last, [])
    out = t.positions('SOLUSDT')
    assert http.last.url.endswith('/fapi/v2/positionRisk')
    check_signed(http.last, [('symbol', 'SOLUSDT')])
    assert out.value[0].position_side == 'LONG'


def test_dual_side_position():
    t, http = make('dual_side_true')
    assert t.dual_side_position().value is True
    assert http.last.url.endswith('/fapi/v1/positionSide/dual')
    check_signed(http.last, [])


def test_open_orders_and_algo_orders():
    t, http = make('open_orders', 'open_algo_orders_list', 'open_algo_orders_wrapped')
    oo = t.open_orders('SOLUSDT')
    assert oo.ok and oo.value[0].type == 'STOP_MARKET'
    assert http.last.url.endswith('/fapi/v1/openOrders')
    check_signed(http.last, [('symbol', 'SOLUSDT')])
    ao = t.open_algo_orders('SOLUSDT')
    assert ao.ok and ao.value[0].client_algo_id == ACID
    assert http.last.url.endswith('/fapi/v1/openAlgoOrders')
    check_signed(http.last, [('symbol', 'SOLUSDT')])
    assert t.open_algo_orders().value[0].algo_id == 2146761
    check_signed(http.last, [])


def test_open_algo_orders_unreadable_is_unknown_not_empty():
    t, _ = make('open_algo_orders_bad_shape', WireTimeout(), 'err_path_invalid')
    a = t.open_algo_orders()
    assert a.kind is ReadKind.UNKNOWN and a.value is None and a.unknown_reason == 'malformed'
    b = t.open_algo_orders()
    assert b.kind is ReadKind.UNKNOWN and b.value is None and b.unknown_reason == 'timeout'
    c = t.open_algo_orders()       # "endpoint unsupported" is a typed refusal; the adapter decides what it proves
    assert c.kind is ReadKind.REJECTED and c.error.category is ErrorCategory.ENDPOINT_UNSUPPORTED


def test_user_trades():
    t, http = make('user_trades')
    out = t.user_trades('SOLUSDT', order_id=4000000100, limit=100)
    assert out.ok and sum(f.qty for f in out.value) == D('10')
    assert http.last.url.endswith('/fapi/v1/userTrades')
    check_signed(http.last, [('symbol', 'SOLUSDT'), ('orderId', '4000000100'), ('limit', '100')])


@pytest.mark.parametrize('kw', [dict(order_id=0), dict(order_id='1'), dict(limit=1001), dict(from_id=-1),
                                dict(start_ms=1, end_ms=1 + 7 * 86400000 + 1), dict(start_ms=5, end_ms=4)])
def test_user_trades_validation(kw):
    t, http = make()
    with pytest.raises(VenueInputError):
        t.user_trades('SOLUSDT', **kw)
    assert http.requests == []


def test_recv_window_and_clock_are_injected():
    t = BinanceTestnetTransport(environment='testnet', http=FakeHttp('account_v2'), clock=lambda: 1700000000123,
                                position_mode=PositionMode.HEDGE, recv_window_ms=3000,
                                credentials=make()[0]._credentials)
    t.account()
    check_signed(t._http.last, [], recv=3000, now=1700000000123)


def test_bad_clock_refused_before_send():
    t, http = make(clock=lambda: 1700000000000.5)
    with pytest.raises(VenueInputError):
        t.account()
    assert http.requests == []


def test_signed_call_without_credentials_is_typed_and_sends_nothing():
    t, http = make(creds=False)
    for call in (t.account, t.positions, lambda: t.query_order('SOLUSDT', CID), t.key_digest):
        with pytest.raises(CredentialsUnavailable):
            call()
    assert http.requests == []


def test_failing_credential_source_is_typed_and_sends_nothing():
    class Broken:
        def api_key(self): raise OSError('vault locked ' + DUMMY_SECRET)
        def sign(self, payload): raise OSError('x')
    t = BinanceTestnetTransport(environment='testnet', http=FakeHttp(), clock=lambda: NOW_MS,
                                position_mode=PositionMode.HEDGE, credentials=Broken())
    with pytest.raises(CredentialsUnavailable) as ei:
        t.account()
    assert DUMMY_SECRET not in str(ei.value) and ei.value.__cause__ is None and ei.value.__suppress_context__


def test_key_digest_from_transport():
    t, http = make()
    assert t.key_digest() == key_digest(DUMMY_KEY) and http.requests == []


# ---------- order placement: shapes ----------

def test_place_market_hedge_shape_and_cid_passthrough():
    t, http = make('order_market_filled')
    out = t.place_market('SOLUSDT', 'BUY', 'LONG', D('10'), CID, reduce_only=False)
    assert (http.last.method, http.last.url) == ('POST', TESTNET_BASE_URL + '/fapi/v1/order')
    check_signed(http.last, [('symbol', 'SOLUSDT'), ('side', 'BUY'), ('positionSide', 'LONG'), ('type', 'MARKET'),
                             ('quantity', '10'), ('newClientOrderId', CID), ('newOrderRespType', 'RESULT')])
    assert out.kind is K.FINAL and out.client_id == CID and out.record.client_order_id == CID
    assert out.executed_qty == D('10') and out.rate.orders('10s') == 1 and out.rate.weight('1m') == 20


def test_place_market_close_hedge_never_sends_reduce_only():
    t, http = make(raw(200, b'{}'))
    t.place_market('SOLUSDT', 'SELL', 'LONG', D('10'), CID, reduce_only=True)
    names = [k for k, _ in parse_qsl(http.last.query)]
    assert 'reduceOnly' not in names and ('positionSide', 'LONG') in parse_qsl(http.last.query)


def test_place_market_one_way_shapes():
    t, http = make(raw(200, b'{}'), raw(200, b'{}'), mode=PositionMode.ONE_WAY)
    t.place_market('SOLUSDT', 'BUY', 'LONG', D('10'), CID, reduce_only=False)
    check_signed(http.last, [('symbol', 'SOLUSDT'), ('side', 'BUY'), ('type', 'MARKET'), ('quantity', '10'),
                             ('newClientOrderId', CID), ('newOrderRespType', 'RESULT')])
    t.place_market('SOLUSDT', 'SELL', 'LONG', D('10'), CID, reduce_only=True)
    check_signed(http.last, [('symbol', 'SOLUSDT'), ('side', 'SELL'), ('reduceOnly', 'true'), ('type', 'MARKET'),
                             ('quantity', '10'), ('newClientOrderId', CID), ('newOrderRespType', 'RESULT')])


@pytest.mark.parametrize('side,pos,ro', [('SELL', 'LONG', False), ('BUY', 'LONG', True), ('BUY', 'SHORT', False),
                                         ('SELL', 'SHORT', True)])
def test_reduce_only_must_match_closing_side(side, pos, ro):
    t, http = make()
    with pytest.raises(VenueInputError):
        t.place_market('SOLUSDT', side, pos, D('1'), CID, reduce_only=ro)
    assert http.requests == []


@pytest.mark.parametrize('qty', [1, 1.0, D('0'), D('-1'), D('NaN'), D('Infinity'), '1', True])
def test_quantity_must_be_positive_decimal(qty):
    t, http = make()
    with pytest.raises(VenueInputError):
        t.place_market('SOLUSDT', 'BUY', 'LONG', qty, CID, reduce_only=False)
    assert http.requests == []


def test_quantity_formatting_is_plain():
    t, http = make(raw(200, b'{}'), raw(200, b'{}'))
    t.place_market('BTCUSDT', 'BUY', 'LONG', D('0.0100'), CID, reduce_only=False)
    assert ('quantity', '0.01') in parse_qsl(http.last.query)
    t.place_market('BTCUSDT', 'BUY', 'LONG', D('1E+2'), CID, reduce_only=False)
    assert ('quantity', '100') in parse_qsl(http.last.query)


@pytest.mark.parametrize('cid', ['', 'x' * 37, 'has space', 'bad!char', 'é', None, 123])
def test_client_id_grammar(cid):
    t, http = make()
    with pytest.raises(VenueInputError):
        t.place_market('SOLUSDT', 'BUY', 'LONG', D('1'), cid, reduce_only=False)
    assert http.requests == []


@pytest.mark.parametrize('cid', ['a', 'x' * 36, 'zb-es-ABC.def:ghi/jkl_mno-PQR'])
def test_client_id_passthrough_verbatim(cid):
    t, http = make(raw(200, b'{}'))
    t.place_market('SOLUSDT', 'BUY', 'LONG', D('1'), cid, reduce_only=False)
    assert dict(parse_qsl(http.last.query))['newClientOrderId'] == cid


def test_place_stop_classic_shape():
    t, http = make('order_stop_new')
    out = t.place_stop_market('SOLUSDT', 'LONG', D('10'), D('211.40'), SCID, route=StopRoute.CLASSIC)
    assert http.last.url.endswith('/fapi/v1/order') and http.last.method == 'POST'
    check_signed(http.last, [('symbol', 'SOLUSDT'), ('side', 'SELL'), ('positionSide', 'LONG'),
                             ('type', 'STOP_MARKET'), ('quantity', '10'), ('stopPrice', '211.4'),
                             ('workingType', 'MARK_PRICE'), ('newClientOrderId', SCID),
                             ('newOrderRespType', 'RESULT')])
    assert out.kind is K.KNOWN and out.record.status == 'NEW' and out.executed_qty is None


def test_place_stop_algo_shape_short_side():
    t, http = make(raw(200, b'{}'))
    t.place_stop_market('SOLUSDT', 'SHORT', D('5'), D('231'), ACID, route=StopRoute.ALGO)
    assert http.last.url.endswith('/fapi/v1/algoOrder') and http.last.method == 'POST'
    check_signed(http.last, [('algoType', 'CONDITIONAL'), ('symbol', 'SOLUSDT'), ('side', 'BUY'),
                             ('positionSide', 'SHORT'), ('type', 'STOP_MARKET'), ('quantity', '5'),
                             ('triggerPrice', '231'), ('workingType', 'MARK_PRICE'), ('clientAlgoId', ACID)])


def test_place_stop_algo_parses_record():
    t, _ = make('algo_order_new')
    out = t.place_stop_market('SOLUSDT', 'LONG', D('10'), D('211.40'), ACID, route=StopRoute.ALGO)
    assert out.kind is K.KNOWN and out.endpoint == 'algo' and out.record.algo_id == 2146760


def test_place_stop_one_way_is_reduce_only():
    t, http = make(raw(200, b'{}'), raw(200, b'{}'), mode=PositionMode.ONE_WAY)
    t.place_stop_market('SOLUSDT', 'LONG', D('10'), D('211.40'), SCID, route=StopRoute.CLASSIC)
    q = parse_qsl(http.last.query)
    assert ('reduceOnly', 'true') in q and 'positionSide' not in dict(q)
    t.place_stop_market('SOLUSDT', 'LONG', D('10'), D('211.40'), ACID, route=StopRoute.ALGO)
    q = parse_qsl(http.last.query)
    assert ('reduceOnly', 'true') in q and 'positionSide' not in dict(q)


def test_stop_route_required():
    t, http = make()
    with pytest.raises(VenueInputError):
        t.place_stop_market('SOLUSDT', 'LONG', D('10'), D('211.40'), SCID, route='algo')
    assert http.requests == []


def test_classic_stop_refused_with_algo_hint_and_no_hidden_second_send():
    t, http = make('err_algo_switch')
    out = t.place_stop_market('SOLUSDT', 'LONG', D('10'), D('211.40'), SCID, route=StopRoute.CLASSIC)
    assert out.kind is K.REJECTED and out.error.code == -4120 and out.error.category is ErrorCategory.ALGO_REQUIRED
    assert out.error.suggests_algo_route is True
    assert len(http.requests) == 1


@pytest.mark.parametrize('fx,hint', [('err_invalid_order_type', True), ('err_would_trigger', False)])
def test_algo_hint_only_for_legacy_fallback_codes(fx, hint):
    t, _ = make(fx)
    out = t.place_stop_market('SOLUSDT', 'LONG', D('10'), D('211.40'), SCID, route=StopRoute.CLASSIC)
    assert out.kind is K.REJECTED and out.error.suggests_algo_route is hint


def test_market_order_refusal_never_has_algo_hint():
    t, _ = make('err_invalid_order_type')
    out = t.place_market('SOLUSDT', 'BUY', 'LONG', D('1'), CID, reduce_only=False)
    assert out.kind is K.REJECTED and out.error.suggests_algo_route is False


# ---------- order placement: outcome typing ----------

def test_market_not_final_is_known():
    t, _ = make('order_market_new')
    out = t.place_market('SOLUSDT', 'BUY', 'LONG', D('10'), CID, reduce_only=False)
    assert out.kind is K.KNOWN and out.executed_qty is None


def test_market_expired_partial_is_final_with_executed_qty():
    t, _ = make('order_market_expired_partial')
    out = t.place_market('SOLUSDT', 'BUY', 'LONG', D('10'), CID, reduce_only=False)
    assert out.kind is K.FINAL and out.executed_qty == D('4')


@pytest.mark.parametrize('answer,reason', [
    (WireTimeout(), 'timeout'),
    (WireConnectionError(), 'connection'),
    (ConnectionResetError('reset by peer'), 'transport_error'),
    (RuntimeError('boom'), 'transport_error'),
    ('err_502_html', 'http_5xx'),
    ('err_busy_503', 'http_5xx'),
    ('err_backend_timeout', 'ambiguous_code'),
    ('err_unexpected_resp', 'ambiguous_code'),
    ('err_unknown_1000', 'ambiguous_code'),
    ('ok_non_json', 'unreadable_body'),
    ('ok_empty', 'unreadable_body'),
    ('err_403_html', 'unreadable_error_body'),
    (raw(302, b''), 'http_status_unexpected'),
    (raw(200, b'{"code": 7, "msg": "?"}'), 'unexpected_code'),
    (raw(200, b'{"orderId": 1}'), 'malformed'),
    ('not an HttpResponse', None),
])
def test_ambiguous_answers_are_unknown_never_failed(answer, reason):
    if answer == 'not an HttpResponse':
        answer, reason = HttpResponse('200', {}, b'{}'), 'bad_http_response'
    t, http = make(answer)
    out = t.place_market('SOLUSDT', 'BUY', 'LONG', D('10'), CID, reduce_only=False)
    assert out.kind is K.UNKNOWN and out.unknown_reason == reason
    assert out.executed_qty is None and out.client_id == CID
    assert len(http.requests) == 1                     # no internal retry


def test_unknown_keeps_ambiguous_error_evidence():
    t, _ = make('err_busy_503')
    out = t.place_market('SOLUSDT', 'BUY', 'LONG', D('10'), CID, reduce_only=False)
    assert out.kind is K.UNKNOWN and out.error.code == -1008 and out.http_status == 503
    assert out.rate.retry_after_s == 5


def test_echo_mismatch_is_unknown():
    t, _ = make('order_market_filled')
    out = t.place_market('SOLUSDT', 'BUY', 'LONG', D('10'), 'zb-SOMEOTHERID', reduce_only=False)
    assert out.kind is K.UNKNOWN and out.unknown_reason == 'echo_mismatch' and out.record is None


def test_symbol_echo_mismatch_is_unknown():
    t, _ = make('order_market_filled')
    out = t.place_market('BTCUSDT', 'BUY', 'LONG', D('10'), CID, reduce_only=False)
    assert out.kind is K.UNKNOWN and out.unknown_reason == 'echo_mismatch'


@pytest.mark.parametrize('fx,code,cat', [
    ('err_timestamp', -1021, ErrorCategory.TIMESTAMP),
    ('err_signature', -1022, ErrorCategory.AUTH),
    ('err_margin', -2019, ErrorCategory.INSUFFICIENT_MARGIN),
    ('err_reduce_only', -2022, ErrorCategory.REDUCE_ONLY),
    ('err_position_side', -4061, ErrorCategory.POSITION_MODE),
    ('err_param_not_required', -1106, ErrorCategory.BAD_REQUEST),
    ('err_unlisted_4xxx', -4999, ErrorCategory.VENUE_RULE),
    ('err_unlisted_other', -3999, ErrorCategory.UNMAPPED),
    ('err_429', -1003, ErrorCategory.RATE_LIMIT),
    ('err_no_such_order', -2013, ErrorCategory.NOT_FOUND),       # not-found on a PLACE is just a refusal
])
def test_clean_refusals_are_rejected_with_code(fx, code, cat):
    t, _ = make(fx)
    out = t.place_market('SOLUSDT', 'BUY', 'LONG', D('10'), CID, reduce_only=False)
    assert out.kind is K.REJECTED and out.error.code == code and out.error.category is cat
    assert out.not_found is None and out.executed_qty is None


def test_timestamp_rejection_is_retryable_after_resync():
    t, _ = make('err_timestamp')
    out = t.account()
    assert out.kind is ReadKind.REJECTED and out.error.retryable_after_fix


def test_http_429_and_418_carry_retry_after():
    t, _ = make('err_429', 'err_418_html')
    a = t.place_market('SOLUSDT', 'BUY', 'LONG', D('1'), CID, reduce_only=False)
    assert a.kind is K.REJECTED and a.rate.retry_after_s == 7 and a.rate.weight('1m') == 2401
    b = t.place_market('SOLUSDT', 'BUY', 'LONG', D('1'), CID, reduce_only=False)
    assert b.kind is K.REJECTED and b.error.category is ErrorCategory.RATE_LIMIT and b.rate.retry_after_s == 120


def test_http_404_is_endpoint_unsupported():
    t, _ = make('err_404_html')
    out = t.open_algo_orders()
    assert out.kind is ReadKind.REJECTED and out.error.category is ErrorCategory.ENDPOINT_UNSUPPORTED


# ---------- query / cancel by client id, not-found evidence ----------

def test_query_order_shape_and_final():
    t, http = make('order_query_filled')
    out = t.query_order('SOLUSDT', CID)
    assert (http.last.method, http.last.url) == ('GET', TESTNET_BASE_URL + '/fapi/v1/order')
    check_signed(http.last, [('symbol', 'SOLUSDT'), ('origClientOrderId', CID)])
    assert out.kind is K.FINAL and out.executed_qty == D('10')


def test_query_not_found_is_typed_evidence_not_never_filled():
    t, _ = make('err_no_such_order')
    out = t.query_order('SOLUSDT', CID)
    assert out.kind is K.NOT_FOUND and out.record is None and out.executed_qty is None
    ev = out.not_found
    assert ev.evidence_type is NotFoundEvidenceType.QUERY_NO_SUCH_ORDER
    assert (ev.endpoint, ev.operation, ev.lookup_field, ev.client_id, ev.code, ev.matched_by) == \
        ('classic', 'query', 'origClientOrderId', CID, -2013, 'code')
    assert ev.proves_never_filled is False and ev.executed_qty is None


def test_query_timeout_is_unknown_not_not_found():
    t, _ = make(WireTimeout())
    out = t.query_order('SOLUSDT', CID)
    assert out.kind is K.UNKNOWN and out.not_found is None


def test_query_algo_shapes_and_states():
    t, http = make('algo_order_triggered', 'algo_order_finished')
    a = t.query_algo_order(ACID)
    assert (http.last.method, http.last.url) == ('GET', TESTNET_BASE_URL + '/fapi/v1/algoOrder')
    check_signed(http.last, [('clientAlgoId', ACID)])
    assert a.kind is K.KNOWN and a.record.actual_order_id == '4000000222'      # TRIGGERED is not final
    b = t.query_algo_order(ACID)
    assert b.kind is K.FINAL and b.record.algo_status == 'FINISHED'


def test_query_algo_not_found_by_code_and_by_message():
    t, _ = make('err_no_such_order', 'err_algo_not_exist_msg')
    a = t.query_algo_order(ACID)
    assert a.kind is K.NOT_FOUND and a.not_found.evidence_type is NotFoundEvidenceType.ALGO_QUERY_NOT_FOUND
    assert a.not_found.matched_by == 'code' and a.not_found.lookup_field == 'clientAlgoId'
    b = t.query_algo_order(ACID)
    assert b.kind is K.NOT_FOUND and b.not_found.matched_by == 'message' and b.not_found.code == -3998


def test_classic_query_never_matches_by_message():
    t, _ = make('err_algo_not_exist_msg')
    out = t.query_order('SOLUSDT', CID)
    assert out.kind is K.REJECTED and out.not_found is None


def test_auth_error_is_never_not_found_even_with_matching_text():
    t, _ = make(raw(400, b'{"code": -2015, "msg": "Invalid API-key, IP, or permissions; order does not exist"}'))
    out = t.query_algo_order(ACID)
    assert out.kind is K.REJECTED and out.error.category is ErrorCategory.AUTH


def test_cancel_order_shape_and_final():
    t, http = make('order_cancel_canceled')
    out = t.cancel_order('SOLUSDT', SCID)
    assert (http.last.method, http.last.url) == ('DELETE', TESTNET_BASE_URL + '/fapi/v1/order')
    check_signed(http.last, [('symbol', 'SOLUSDT'), ('origClientOrderId', SCID)])
    assert out.kind is K.FINAL and out.record.status == 'CANCELED'


@pytest.mark.parametrize('fx,ev_type', [('err_cancel_unknown', NotFoundEvidenceType.CANCEL_UNKNOWN_ORDER),
                                        ('err_no_such_order', NotFoundEvidenceType.CANCEL_NO_SUCH_ORDER)])
def test_cancel_not_found_is_evidence_not_success(fx, ev_type):
    t, _ = make(fx)
    out = t.cancel_order('SOLUSDT', SCID)
    assert out.kind is K.NOT_FOUND and out.not_found.evidence_type is ev_type
    assert out.not_found.operation == 'cancel' and out.executed_qty is None


def test_cancel_minus_4120_is_not_gone():
    # Legacy cancel() treated -4120 as "already gone". Not ported: it is a refusal (use the algo endpoint).
    t, _ = make('err_algo_switch')
    out = t.cancel_order('SOLUSDT', SCID)
    assert out.kind is K.REJECTED and out.error.category is ErrorCategory.ALGO_REQUIRED and out.not_found is None


def test_cancel_algo_ack_shape():
    t, http = make('algo_cancel_ack')
    out = t.cancel_algo_order(ACID)
    assert (http.last.method, http.last.url) == ('DELETE', TESTNET_BASE_URL + '/fapi/v1/algoOrder')
    check_signed(http.last, [('clientAlgoId', ACID)])
    assert out.kind is K.ACKNOWLEDGED and out.record is None and out.executed_qty is None


def test_cancel_algo_ack_for_another_id_is_unknown():
    t, _ = make('algo_cancel_ack')
    out = t.cancel_algo_order('zb-es-OTHER')
    assert out.kind is K.UNKNOWN and out.unknown_reason == 'echo_mismatch'


def test_cancel_algo_full_record_and_not_found():
    t, _ = make('algo_order_finished', 'err_cancel_unknown', 'err_algo_not_exist_msg')
    assert t.cancel_algo_order(ACID).kind is K.FINAL
    b = t.cancel_algo_order(ACID)
    assert b.kind is K.NOT_FOUND and b.not_found.evidence_type is NotFoundEvidenceType.ALGO_CANCEL_NOT_FOUND
    c = t.cancel_algo_order(ACID)
    assert c.kind is K.NOT_FOUND and c.not_found.matched_by == 'message'


def test_cancel_timeout_is_unknown():
    t, _ = make(WireTimeout(), WireConnectionError())
    assert t.cancel_order('SOLUSDT', SCID).kind is K.UNKNOWN
    assert t.cancel_algo_order(ACID).kind is K.UNKNOWN


# ---------- reads: outcome typing ----------

@pytest.mark.parametrize('answer,reason', [(WireTimeout(), 'timeout'), ('err_502_html', 'http_5xx'),
                                           ('ok_non_json', 'unreadable_body'),
                                           (raw(200, b'[{"symbol": "SOLUSDT"}]'), 'malformed')])
def test_read_failures_are_unknown_never_empty(answer, reason):
    t, _ = make(answer)
    out = t.positions()
    assert out.kind is ReadKind.UNKNOWN and out.value is None and out.unknown_reason == reason


def test_empty_list_is_a_real_empty_read():
    t, _ = make(raw(200, b'[]'))
    out = t.positions()
    assert out.ok and out.value == ()


# ---------- regression: "$" also matches before a trailing newline; every validator uses fullmatch ----------

@pytest.mark.parametrize('call', [
    lambda t: t.place_market('SOLUSDT', 'BUY', 'LONG', D('1'), CID + '\n', reduce_only=False),
    lambda t: t.query_algo_order(ACID + '\n'),
    lambda t: t.income(income_type='FUNDING_FEE\n'),
])
def test_trailing_newline_refused(call):
    t, http = make()
    with pytest.raises(VenueInputError):
        call(t)
    assert http.requests == []


# ---------- a duplicate client id means the order EXISTS (Step 0 / Cowork): UNKNOWN, never REJECTED ----------

DUP = raw(400, b'{"code": -4116, "msg": "ClientOrderId is duplicated."}')
DUP_TEXT = raw(400, b'{"code": -2010, "msg": "Duplicate order sent."}')


@pytest.mark.parametrize('answer', [DUP, DUP_TEXT])
@pytest.mark.parametrize('call', [
    lambda t: t.place_market('SOLUSDT', 'BUY', 'LONG', D('1'), CID, reduce_only=False),
    lambda t: t.place_stop_market('SOLUSDT', 'LONG', D('1'), D('2'), SCID, route=StopRoute.CLASSIC),
    lambda t: t.place_stop_market('SOLUSDT', 'LONG', D('1'), D('2'), ACID, route=StopRoute.ALGO),
])
def test_duplicate_client_id_on_place_is_unknown(answer, call):
    t, http = make(answer)
    out = call(t)
    assert out.kind is K.UNKNOWN and out.unknown_reason == 'duplicate_client_id' and out.executed_qty is None
    assert out.error is not None and len(http.requests) == 1


def test_duplicate_text_on_cancel_stays_a_refusal():
    t, _ = make(DUP)
    assert t.cancel_order('SOLUSDT', SCID).kind is K.REJECTED
