"""mark_price (premiumIndex, for the runner's intra-candle mark poll) and fills by time window (userTrades paging, for
REC-02 attribution of manual closes / late fills). Fake HTTP only, DUMMY keys, no network."""
import json

import pytest

from ncv_support import NOW_MS, D, make, query_pairs, raw
from newcore.ports import venue as P
from newcore.ports.values import PortValueError
from newcore.venue import testnet_venue as TV
from newcore.venue.testnet_venue import TestnetAccountReader, TestnetVenue
from newcore.venue.wire import WireTimeout

T0 = NOW_MS - 3_600_000


def ok(obj, weight=1):
    return raw(200, json.dumps(obj).encode(), {'X-MBX-USED-WEIGHT-1M': str(weight)})


def premium(symbol='SOLUSDT', mark='220.51', t=NOW_MS - 7):
    return {'symbol': symbol, 'markPrice': mark, 'indexPrice': '220.40', 'estimatedSettlePrice': '220.30',
            'lastFundingRate': '0.00010000', 'interestRate': '0.00010000', 'nextFundingTime': NOW_MS + 600_000,
            'time': t}


def reader(*script):
    t, http = make(*script)
    return TestnetAccountReader(t, lambda: NOW_MS), http


# ---------------------------------------------------------------------------------------------- mark_price
def test_mark_price_ok_is_one_public_request_with_the_server_time():
    r, http = reader(ok(premium()))
    out = r.mark_price('SOLUSDT')
    assert out.kind is P.ReadKind.OK and out.value == (TV.MarkQuote('SOLUSDT', D('220.51'), NOW_MS - 7),)
    q, = http.requests
    assert q.method == 'GET' and q.url.endswith('/fapi/v1/premiumIndex') and query_pairs(q) == [('symbol', 'SOLUSDT')]
    assert not q.signed


@pytest.mark.parametrize('answer,kind,detail', [
    (raw(503, b'busy'), P.ReadKind.UNKNOWN, None),
    (WireTimeout('t'), P.ReadKind.UNKNOWN, None),
    (ok({'symbol': 'SOLUSDT', 'time': NOW_MS}), P.ReadKind.UNKNOWN, 'malformed'),
    (ok({**premium(), 'markPrice': '0'}), P.ReadKind.UNKNOWN, 'malformed'),
    (ok([premium()]), P.ReadKind.UNKNOWN, 'malformed'),
    (ok(premium(symbol='BTCUSDT')), P.ReadKind.UNKNOWN, 'unrepresentable'),
    (raw(400, b'{"code": -1121, "msg": "Invalid symbol."}'), P.ReadKind.REJECTED, None),
])
def test_mark_price_is_typed_and_never_retried(answer, kind, detail):
    r, http = reader(answer)
    out = r.mark_price('SOLUSDT')
    assert out.kind is kind and len(http.requests) == 1
    if detail:
        assert out.detail == detail
    assert out.kind is not P.ReadKind.OK or out.value


def test_mark_price_records_into_a_cassette_and_replays_offline():
    from newcore.venue.cassette import CassettePlayer, CassetteRecorder
    from newcore.venue.credentials import StaticCredentials
    from newcore.venue.transport import BinanceTestnetTransport, PositionMode
    from ncv_support import DUMMY_KEY, DUMMY_SECRET, FakeHttp
    rec = CassetteRecorder(FakeHttp(ok(premium())), redact=(DUMMY_KEY, DUMMY_SECRET))

    def reader_over(http):
        t = BinanceTestnetTransport(environment='testnet', http=http, clock=lambda: NOW_MS,
                                    position_mode=PositionMode.HEDGE,
                                    credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
        return TestnetAccountReader(t, lambda: NOW_MS)
    first = reader_over(rec).mark_price('SOLUSDT')
    doc = json.loads(rec.to_json())
    it, = doc['interactions']
    assert it['request']['url'].endswith('/fapi/v1/premiumIndex') and json.loads(it['response']['body_text'])[
        'markPrice'] == '220.51'
    player = CassettePlayer(doc)
    again = reader_over(player).mark_price('SOLUSDT')
    player.assert_exhausted()
    assert again == first


# ---------------------------------------------------------------------------------------------- fills by window
def trade(i, *, t=None, side='BUY', ps='LONG', symbol='SOLUSDT', qty='1', order=5000):
    return {'buyer': side == 'BUY', 'commission': '0.011', 'commissionAsset': 'USDT', 'id': i, 'maker': False,
            'orderId': order, 'price': '220.5', 'qty': qty, 'quoteQty': str(D(qty) * D('220.5')),
            'realizedPnl': '0.5' if side == 'SELL' else '0', 'side': side, 'positionSide': ps, 'symbol': symbol,
            'time': T0 + i if t is None else t}


def venue(*script):
    t, http = make(*script)
    return TestnetVenue(t, lambda: NOW_MS), http


def test_window_fills_carry_side_and_are_sorted():
    v, http = venue(ok([trade(1, t=T0 + 10), trade(2, side='SELL', ps='LONG', t=T0 + 50),
                        trade(3, side='BUY', ps='SHORT', t=T0 + 90)]))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.OK
    assert [(f.trade_id, f.side, f.position_side) for f in out.value] == [('1', 'BUY', 'LONG'), ('2', 'SELL', 'LONG'),
                                                                          ('3', 'BUY', 'SHORT')]
    f = out.value[1]
    assert (f.exchange_order_id, f.qty, f.price, f.fee, f.fee_asset, f.realized_pnl, f.at_ms) == \
        ('5000', D('1'), D('220.5'), D('0.011'), 'USDT', D('0.5'), T0 + 50)
    q, = http.requests
    assert dict(query_pairs(q))['startTime'] == str(T0) and dict(query_pairs(q))['endTime'] == str(NOW_MS)


def test_a_full_page_continues_by_from_id_and_stops_at_the_window_end(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', 3)
    v, http = venue(ok([trade(1), trade(2), trade(3)]), ok([trade(4), trade(5), trade(6, t=NOW_MS + 5)]))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.OK and [f.trade_id for f in out.value] == ['1', '2', '3', '4', '5']
    second = dict(query_pairs(http.requests[1]))
    assert second['fromId'] == '4' and 'startTime' not in second and 'endTime' not in second


def test_an_error_mid_paging_is_unknown_never_a_partial_list(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', 2)
    v, _ = venue(ok([trade(1), trade(2)]), raw(503, b'busy'))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.UNKNOWN and out.value is None


def test_a_rejected_page_is_passed_through(monkeypatch):
    v, _ = venue(raw(400, b'{"code": -1121, "msg": "Invalid symbol."}'))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.REJECTED and out.error_code == -1121


@pytest.mark.parametrize('pages,detail', [
    ([[trade(2), trade(1)]], 'out_of_order'),
    ([[trade(1, symbol='BTCUSDT')]], 'out_of_order'),
])
def test_a_page_that_is_not_one_ordered_run_is_unknown(pages, detail):
    v, _ = venue(*[ok(p) for p in pages])
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == detail


def test_a_from_id_page_that_goes_back_is_unknown(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', 2)
    v, _ = venue(ok([trade(1), trade(2)]), ok([trade(2, qty='9'), trade(3)]))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'conflicting_trade'  # id 2 back, with other fields


def test_a_from_id_page_with_an_unseen_older_trade_is_unknown(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', 2)
    v, _ = venue(ok([trade(2), trade(3)]), ok([trade(1), trade(4)]))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'out_of_order'      # fromId 4 got an unseen id 1


def test_a_conflicting_duplicate_trade_in_a_page_is_unknown():
    v, _ = venue(ok([trade(1), trade(1, qty='9'), trade(2)]))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'malformed'


def test_an_identical_duplicate_trade_in_a_page_is_deduped_and_counted():          # Cowork 6068372233
    v, _ = venue(ok([trade(1), trade(1), trade(2)]))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.OK and [f.trade_id for f in out.value] == ['1', '2']
    assert out.detail == 'complete pages=1 dups=1'


def test_a_trade_seen_again_outside_its_window_is_not_counted_twice(monkeypatch):
    monkeypatch.setattr(TV, 'FILL_WINDOW_MS', 50)
    v, http = venue(ok([trade(1, t=T0 + 10), trade(2, t=T0 + 50)]), ok([trade(2, t=T0 + 50), trade(3, t=T0 + 60)]))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=T0 + 100)
    assert out.kind is P.ReadKind.OK and [f.trade_id for f in out.value] == ['1', '2', '3'] and len(http.requests) == 2
    assert out.detail == 'complete pages=2 dups=1'                       # trade 2 came back in the next window


def test_long_ranges_walk_seven_day_windows(monkeypatch):
    week = TV.FILL_WINDOW_MS + 1
    v, http = venue(ok([]), ok([]), ok([]))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=T0 + 2 * week + 5)
    assert out.kind is P.ReadKind.OK and out.value == () and len(http.requests) == 3
    starts = [int(dict(query_pairs(q))['startTime']) for q in http.requests]
    assert starts == [T0, T0 + week, T0 + 2 * week]


def test_the_page_bound_is_unknown(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', 1)
    monkeypatch.setattr(TV, 'FILL_WINDOW_PAGES', 3)
    v, http = venue(*[ok([trade(i)]) for i in range(1, 5)])
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'paging_bound' and len(http.requests) == 3


@pytest.mark.parametrize('kw', [{'start_ms': None, 'end_ms': NOW_MS}, {'start_ms': NOW_MS, 'end_ms': T0},
                                {'start_ms': T0}, {}])
def test_window_arguments_are_checked(kw):
    v, http = venue()
    with pytest.raises(PortValueError):
        v.fills('SOLUSDT', **kw)
    assert http.requests == []


def test_by_order_and_by_window_cannot_be_mixed():
    v, http = venue()
    with pytest.raises(PortValueError):
        v.fills('SOLUSDT', '5000', start_ms=T0, end_ms=NOW_MS)
    assert http.requests == []


def test_the_by_order_read_is_unchanged():
    v, http = venue(ok([trade(1), trade(2)]))
    out = v.fills('SOLUSDT', '5000')
    assert out.kind is P.ReadKind.OK and isinstance(out.value[0], P.VenueFill)
    assert dict(query_pairs(http.last))['orderId'] == '5000'


@pytest.mark.parametrize('t,detail', [(NOW_MS - 31_000, 'stale_mark'), (NOW_MS + 6_000, 'future_mark')])
def test_m1_a_stale_or_future_mark_is_unknown(t, detail):
    r, _ = reader(ok(premium(t=t)))
    out = r.mark_price('SOLUSDT')
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == detail


def test_m1_a_mark_within_bounds_is_ok():
    r, _ = reader(ok(premium(t=NOW_MS - 29_000)))
    assert r.mark_price('SOLUSDT').kind is P.ReadKind.OK
