"""NC-03 wire layer: strict response parsers, the error-code table and rate-limit header parsing."""
from decimal import Decimal as D

import pytest

from newcore.venue import records as R
from newcore.venue.errors import (ALGO_FALLBACK_CODES, ERROR_CODES, ErrorCategory, NotFoundEvidence,
                                  NotFoundEvidenceType, categorize, scrub_text)
from newcore.venue.wire import parse_rate_limits

from ncv_support import fixture


def body(name):
    return R.decode_json(fixture(name).body)


# ---------- JSON strictness ----------

def test_decode_uses_decimal_never_float():
    v = R.decode_json(b'{"a": 0.1, "b": 2}')
    assert type(v['a']) is D and v['a'] == D('0.1') and type(v['b']) is int


@pytest.mark.parametrize('raw', [b'{"a": NaN}', b'{"a": Infinity}', b'{"a": -Infinity}', b'{"a":1,"a":2}',
                                 b'not json', b'\xff\xfe', b'{"a":', b''])
def test_decode_refuses_bad_json(raw):
    with pytest.raises(R.MalformedResponse):
        R.decode_json(raw)


@pytest.mark.parametrize('v', ['1e5', '+1', ' 1', '1.', '.5', '0x10', 'NaN', '1_000', True, [1], {}])
def test_dec_refuses_non_plain_numbers(v):
    with pytest.raises(R.MalformedResponse):
        R.dec({'x': v}, 'x')


def test_dec_accepts_strings_ints_decimals():
    assert R.dec({'x': '-0.0010'}, 'x') == D('-0.001')
    assert R.dec({'x': 5}, 'x') == D(5)
    assert R.dec({'x': D('1.5')}, 'x') == D('1.5')
    assert R.dec({'x': ''}, 'x', optional=True) is None
    with pytest.raises(R.MalformedResponse):
        R.dec({}, 'x')


# ---------- exchangeInfo ----------

def test_exchange_info_filters():
    info = R.parse_exchange_info(body('exchange_info'))
    sol = info.symbols['SOLUSDT']
    assert (sol.tick_size, sol.step_size, sol.min_qty, sol.min_notional) == (D('0.01'), D('1'), D('1'), D('5'))
    assert (sol.market_step_size, sol.market_max_qty) == (D('1'), D('5000'))
    assert sol.max_num_algo_orders == 10 and 'STOP_MARKET' in sol.order_types
    btc = info.symbols['BTCUSDT']
    assert btc.step_size == D('0.001') and btc.tick_size == D('0.10') and btc.max_num_orders is None
    assert info.server_time_ms == 1759917600000
    assert ('REQUEST_WEIGHT', 'MINUTE', 1, 2400) in info.rate_limits


def test_exchange_info_symbol_missing_filter_is_unknown_not_guessed():
    info = R.parse_exchange_info(body('exchange_info'))
    assert 'ODDUSDT' not in info.symbols and info.unparseable == ('ODDUSDT',)


def test_exchange_info_without_symbols_list_is_malformed():
    with pytest.raises(R.MalformedResponse):
        R.parse_exchange_info({'serverTime': 1, 'symbols': None})


# ---------- klines ----------

def test_klines_parse():
    ks = R.parse_klines(body('klines_4h'))
    assert len(ks) == 3
    k = ks[0]
    assert (k.open_time_ms, k.open, k.high, k.low, k.close) == (1759881600000, D('220.10'), D('224.50'),
                                                                 D('219.00'), D('223.40'))
    assert k.close_time_ms == 1759895999999 and k.trades == 51234
    # The last candle is still forming at NOW (close 1759924799999 > now 1759917600000).
    assert ks[1].is_closed_at(1759917600000) and not ks[2].is_closed_at(1759917600000)


@pytest.mark.parametrize('row', [
    [1, '10', '9', '8', '9', '1', 2, '1', 1, '1', '1', '0'],        # high below open
    [5, '10', '11', '9', '10', '1', 5, '1', 1, '1', '1', '0'],      # close time == open time
    [1, '10', '11', '9', '10', '1', 2, '1', 1, '1'],                # too short
    [1, 10.5, '11', '9', '10', '1', 2, '1', 1, '1', '1', '0'],      # float price
])
def test_kline_bad_rows(row):
    with pytest.raises(R.MalformedResponse):
        R.parse_klines([row])


def test_klines_must_ascend():
    rows = body('klines_4h')
    with pytest.raises(R.MalformedResponse):
        R.parse_klines([rows[1], rows[0]])


# ---------- account / positions ----------

def test_account_parse():
    a = R.parse_account(body('account_v2'))
    assert a.can_trade is True and a.total_wallet_balance == D('5000.12345678')
    assert a.available_balance == D('4913.52345678') and a.assets[0].asset == 'USDT'


def test_positions_parse_hedge():
    rows = R.parse_positions(body('position_risk_hedge'))
    assert [(r.symbol, r.position_side, r.position_amt) for r in rows] == [('SOLUSDT', 'LONG', D('10')),
                                                                           ('SOLUSDT', 'SHORT', D('0'))]
    assert rows[0].leverage == 3 and rows[0].margin_type == 'cross' and rows[0].mark_price == D('221.05')


@pytest.mark.parametrize('patch', [{'positionSide': 'LONG', 'positionAmt': '-1'},
                                   {'positionSide': 'SHORT', 'positionAmt': '1'},
                                   {'positionSide': 'UP'}, {'marginType': 'weird'}, {'positionAmt': None}])
def test_positions_bad_rows(patch):
    rows = body('position_risk_hedge')
    rows[0].update(patch)
    with pytest.raises(R.MalformedResponse):
        R.parse_positions(rows)


def test_positions_duplicate_row_refused():
    rows = body('position_risk_hedge')
    with pytest.raises(R.MalformedResponse):
        R.parse_positions([rows[0], rows[0]])


def test_positions_not_a_list_is_malformed():
    with pytest.raises(R.MalformedResponse):
        R.parse_positions({'code': 0})


# ---------- orders ----------

def test_order_final_and_open():
    filled = R.parse_order(body('order_market_filled'))
    assert filled.is_final and filled.executed_qty == D('10') and filled.avg_price == D('222.3')
    stop = R.parse_order(body('order_stop_new'))
    assert not stop.is_final and stop.reduce_only and stop.stop_price == D('211.40')
    exp = R.parse_order(body('order_market_expired_partial'))
    assert exp.is_final and exp.executed_qty == D('4')     # AUD-03: an EXPIRED record carries the executed qty


@pytest.mark.parametrize('patch', [{'status': 'WEIRD'}, {'executedQty': '11'}, {'reduceOnly': 'true'},
                                   {'orderId': True}, {'side': 'HOLD'},
                                   {'status': 'FILLED', 'executedQty': '9'}])
def test_order_bad_records(patch):
    o = body('order_market_filled')
    o.update(patch)
    with pytest.raises(R.MalformedResponse):
        R.parse_order(o)


def test_algo_records():
    new = R.parse_algo_order(body('algo_order_new'))
    assert new.algo_status == 'NEW' and not new.is_final and new.trigger_price == D('211.40') and new.reduce_only
    trig = R.parse_algo_order(body('algo_order_triggered'))
    assert not trig.is_final and trig.actual_order_id == '4000000222'
    fin = R.parse_algo_order(body('algo_order_finished'))
    assert fin.is_final and fin.actual_order_id == '4000000222' and fin.actual_price == D('211.35')


def test_open_algo_orders_list_or_wrapped():
    assert len(R.parse_open_algo_orders(body('open_algo_orders_list'))) == 1
    assert R.parse_open_algo_orders(body('open_algo_orders_wrapped'))[0].position_side == 'SHORT'
    with pytest.raises(R.MalformedResponse):
        R.parse_open_algo_orders(body('open_algo_orders_bad_shape'))
    with pytest.raises(R.MalformedResponse):
        R.parse_open_algo_orders(None)


def test_open_orders_parse():
    (o,) = R.parse_open_orders(body('open_orders'))
    assert o.client_order_id == 'zb-es-AAAABBBBCCCCDDDDEEEE' and o.working_type == 'MARK_PRICE'


def test_fills_parse():
    fills = R.parse_fills(body('user_trades'))
    assert sum(f.qty for f in fills) == D('10') and sum(f.commission for f in fills) == D('1.1115')
    assert fills[0].order_id == 4000000100 and fills[0].maker is False and fills[0].position_side == 'LONG'


def test_fills_duplicate_id_refused():
    t = body('user_trades')
    with pytest.raises(R.MalformedResponse):
        R.parse_fills([t[0], t[0]])


def test_dual_side_and_server_time():
    assert R.parse_dual_side(body('dual_side_true')) is True
    assert R.parse_server_time(body('server_time')) == 1759917600000
    with pytest.raises(R.MalformedResponse):
        R.parse_dual_side({'dualSidePosition': 'true'})


# ---------- error table ----------

def test_error_table_core_codes():
    expect = {-1021: ErrorCategory.TIMESTAMP, -1022: ErrorCategory.AUTH, -2011: ErrorCategory.NOT_FOUND,
              -2013: ErrorCategory.NOT_FOUND, -4120: ErrorCategory.ALGO_REQUIRED, -1000: ErrorCategory.AMBIGUOUS,
              -1001: ErrorCategory.AMBIGUOUS, -1006: ErrorCategory.AMBIGUOUS, -1007: ErrorCategory.AMBIGUOUS,
              -2021: ErrorCategory.WOULD_TRIGGER, -2022: ErrorCategory.REDUCE_ONLY, -4061: ErrorCategory.POSITION_MODE,
              -5000: ErrorCategory.ENDPOINT_UNSUPPORTED, -1003: ErrorCategory.RATE_LIMIT,
              -4046: ErrorCategory.NO_CHANGE, -4059: ErrorCategory.NO_CHANGE, -4164: ErrorCategory.FILTER}
    for code, cat in expect.items():
        assert ERROR_CODES[code][1] is cat, code
    assert categorize(-4888) == ('UNLISTED_4XXX', ErrorCategory.VENUE_RULE)
    assert categorize(-3999) == ('UNLISTED', ErrorCategory.UNMAPPED)
    assert set(ALGO_FALLBACK_CODES) == {-4120, -1116, -1102, -4136}


def test_not_found_evidence_never_carries_fill():
    ev = NotFoundEvidence(NotFoundEvidenceType.QUERY_NO_SUCH_ORDER, 'classic', 'query', 'origClientOrderId',
                          'zb-x', -2013, 'code', 'Order does not exist.')
    assert ev.executed_qty is None and ev.proves_never_filled is False


def test_scrub_text_blanks_token_runs_and_truncates():
    s = scrub_text('bad key ' + 'A' * 64 + ' tail' + 'x' * 500)
    assert 'A' * 32 not in s and '<redacted>' in s and len(s) <= 240


# ---------- rate-limit headers ----------

def test_rate_limit_headers():
    r = parse_rate_limits({'X-MBX-USED-WEIGHT-1M': '20', 'x-mbx-used-weight-1h': '300',
                           'X-MBX-ORDER-COUNT-10S': '1', 'X-MBX-ORDER-COUNT-1M': '3', 'Retry-After': '7'})
    assert r.weight('1m') == 20 and r.weight('1h') == 300 and r.orders('10s') == 1 and r.orders('1m') == 3
    assert r.retry_after_s == 7


@pytest.mark.parametrize('headers', [None, {}, {'X-MBX-USED-WEIGHT-1M': 'lots'}, {'X-MBX-USED-WEIGHT-1M': '-1'},
                                     {'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT'}, {1: 2}, 'junk'])
def test_rate_limit_headers_never_raise(headers):
    r = parse_rate_limits(headers)
    assert r.weight('1m') is None and r.retry_after_s is None
