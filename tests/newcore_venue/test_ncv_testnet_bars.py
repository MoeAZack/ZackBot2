"""TestnetBarSource (frozen BarSource port) over scripted klines. No network; unsigned reads need no key."""
import json
from decimal import Decimal as D
from urllib.parse import parse_qsl

import pytest

from newcore.ports import bars as B
from newcore.ports import venue as P
from newcore.ports.values import PortValueError
from newcore.venue.testnet_bars import BarGap, TestnetBarSource, find_gaps
from newcore.venue.transport import BinanceTestnetTransport, PositionMode
from newcore.venue.wire import HttpResponse, WireTimeout

H4 = 14_400_000
M1 = 60_000
NOW = 1759924800000 + 2 * 3_600_000          # 2 h into a 4h candle (server time)


def row(open_ms, tf, close='100', closetime=None):
    c = D(close)
    return [open_ms, str(c), str(c + 1), str(c - 1), str(c), '10', closetime if closetime is not None else
            open_ms + tf - 1, '1000', 5, '5', '500', '0']


class KlineVenue:
    """Answers GET /fapi/v1/klines like Binance: rows with open <= endTime, the last `limit`, ascending."""

    def __init__(self, rows, fail_at=None):
        self.rows = sorted(rows, key=lambda r: r[0])
        self.requests, self.fail_at = [], dict(fail_at or {})

    def __call__(self, request):
        self.requests.append(request)
        n = len(self.requests)
        if n in self.fail_at:
            f = self.fail_at[n]
            if isinstance(f, BaseException):
                raise f
            return f
        q = dict(parse_qsl(request.query))
        end, limit = int(q.get('endTime', 10 ** 15)), int(q.get('limit', 500))
        page = [r for r in self.rows if r[0] <= end][-limit:]
        return HttpResponse(200, {'X-MBX-USED-WEIGHT-1M': '2'}, json.dumps(page).encode())


def series(n, tf=H4, end_open=None, **kw):
    """n candles of tf ending with the FORMING one (open <= NOW < open + tf) unless end_open is given."""
    last = end_open if end_open is not None else (NOW // tf) * tf
    return [row(last - tf * i, tf, **kw) for i in range(n - 1, -1, -1)]


def source(http, now=NOW, page=500):
    t = BinanceTestnetTransport(environment='testnet', http=http, clock=lambda: now, position_mode=PositionMode.HEDGE)
    return TestnetBarSource(t, lambda: now, page_limit=page)


def test_conforms_to_the_frozen_port():
    assert isinstance(source(KlineVenue([])), B.BarSource)


def test_closed_bars_only_with_exclusive_close_and_decimals():
    v = KlineVenue(series(6))
    out = source(v).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=5)
    assert out.kind is P.ReadKind.OK and len(out.value) == 5
    last = out.value[-1]
    assert last.close_ms <= NOW and last.close_ms == last.open_ms + H4 and type(last.close) is D
    assert all(b.close_ms == b.open_ms + H4 for b in out.value)
    B.check_closed_bars(out.value, H4, NOW)
    q = dict(parse_qsl(v.requests[0].query))
    assert (q['interval'], q['endTime'], q['limit']) == ('4h', str(NOW - 1), '500') and not v.requests[0].signed


def test_forming_candle_bypass_with_a_future_as_of_is_refused():
    v = KlineVenue(series(6))
    out = source(v).closed_bars('SOLUSDT', H4, as_of_ms=NOW + 10 * H4, limit=10)
    assert out.kind is P.ReadKind.OK
    assert all(b.close_ms <= NOW for b in out.value) and len(out.value) == 5      # the forming one is not there
    assert dict(parse_qsl(v.requests[0].query))['endTime'] == str(NOW - 1)        # the cutoff is server time


def test_venue_returning_a_candle_beyond_end_time_is_unknown():
    rows = series(3)
    rows.append(row(rows[-1][0] + H4, H4))                                         # a candle opening after endTime

    def http(request):
        return HttpResponse(200, {}, json.dumps(rows).encode())
    out = source(http).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=2)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'candle_after_end_time'


def test_earlier_as_of_excludes_later_candles():
    v = KlineVenue(series(6))
    as_of = (NOW // H4) * H4 - H4                                                  # one candle earlier
    out = source(v).closed_bars('SOLUSDT', H4, as_of_ms=as_of, limit=10)
    assert out.kind is P.ReadKind.OK and out.value[-1].close_ms == as_of and len(out.value) == 4


def test_gap_is_typed_never_skipped():
    rows = series(8)
    del rows[3]
    src = source(KlineVenue(rows))
    out = src.closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=7)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'gap' and out.value is None
    (gap,) = src.last_gaps
    assert isinstance(gap, BarGap) and gap.missing == 1 and gap.next_open_ms - gap.after_close_ms == H4


def test_gap_outside_the_requested_window_does_not_matter():
    rows = series(8)
    del rows[1]
    out = source(KlineVenue(rows)).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=4)
    assert out.kind is P.ReadKind.OK and len(out.value) == 4


def test_out_of_order_candles_in_a_page_are_unknown():
    rows = series(5)
    rows[1], rows[2] = rows[2], rows[1]

    def http(request):
        return HttpResponse(200, {}, json.dumps(rows).encode())
    out = source(http).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=3)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'malformed'


def test_duplicate_candle_in_a_page_is_unknown():
    rows = series(5)
    rows.insert(2, list(rows[2]))                                                  # the same open twice

    def http(request):
        return HttpResponse(200, {}, json.dumps(rows).encode())
    out = source(http).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=3)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'malformed'


def test_page_overlapping_the_previous_one_is_unknown():
    rows = series(9)
    pages = iter([rows[5:], rows[1:6]])                 # the 2nd page repeats rows[5], beyond its endTime

    def http(request):
        return HttpResponse(200, {}, json.dumps(next(pages)).encode())
    out = source(http, page=4).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=7)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'candle_after_end_time'


@pytest.mark.parametrize('closetime_delta', [0, -2, 5])
def test_misaligned_candle_is_unknown(closetime_delta):
    rows = series(4)
    rows[1][6] = rows[1][0] + H4 - 1 + closetime_delta if closetime_delta else rows[1][0] + H4   # closeTime wrong
    out = source(KlineVenue(rows)).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=3)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'misaligned_candle'


def test_warmup_paging_is_bounded_and_contiguous():
    v = KlineVenue(series(1200))
    out = source(v, page=150).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=400)
    assert out.kind is P.ReadKind.OK and len(out.value) == 400
    B.check_closed_bars(out.value, H4, NOW)
    assert 3 <= len(v.requests) <= -(-400 // 150) + 1
    ends = [int(dict(parse_qsl(r.query))['endTime']) for r in v.requests]
    assert ends == sorted(ends, reverse=True) and ends[0] == NOW - 1


def test_default_page_covers_the_ema200_4h_warmup_in_one_request():
    v = KlineVenue(series(800))
    out = source(v).closed_bars('BTCUSDT', H4, as_of_ms=NOW, limit=400)
    assert out.kind is P.ReadKind.OK and len(out.value) == 400 and len(v.requests) == 1


def test_short_history_returns_fewer_bars():
    v = KlineVenue(series(100))
    out = source(v, page=60).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=400)
    assert out.kind is P.ReadKind.OK and len(out.value) == 99 and len(v.requests) == 2


def test_stuck_paging_hits_the_bound():
    page = series(5)

    def http(request):                                  # always the same full page: no progress backwards
        return HttpResponse(200, {}, json.dumps(page).encode())
    out = source(http, page=5).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=20)
    assert out.kind is P.ReadKind.UNKNOWN


@pytest.mark.parametrize('fail,kind', [(WireTimeout(), P.ReadKind.UNKNOWN),
                                       (HttpResponse(400, {}, b'{"code": -1121, "msg": "Invalid symbol."}'),
                                        P.ReadKind.REJECTED)])
def test_a_failed_page_makes_the_whole_answer_not_ok(fail, kind):
    v = KlineVenue(series(1200), fail_at={2: fail})
    out = source(v, page=150).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=400)
    assert out.kind is kind and out.value is None


@pytest.mark.parametrize('args', [('sol', H4, NOW, 5), ('SOLUSDT', 120_000, NOW, 5), ('SOLUSDT', H4, NOW, 0),
                                  ('SOLUSDT', H4, NOW, 1501), ('SOLUSDT', 7 * M1, NOW, 5)])
def test_bad_requests_refused_before_any_read(args):
    v = KlineVenue([])
    with pytest.raises(PortValueError):
        source(v).closed_bars(args[0], args[1], as_of_ms=args[2], limit=args[3])
    assert v.requests == []


def test_one_minute_bars():
    v = KlineVenue(series(4, tf=M1))
    out = source(v).closed_bars('SOLUSDT', M1, as_of_ms=NOW, limit=2)
    assert out.kind is P.ReadKind.OK and dict(parse_qsl(v.requests[0].query))['interval'] == '1m'


def test_find_gaps_counts_missing_candles():
    bars = tuple(B.Bar(open_ms=o, close_ms=o + H4, open=D('1'), high=D('1'), low=D('1'), close=D('1'),
                       volume=D('0')) for o in (NOW // H4 * H4 + k * H4 for k in (0, 1, 4)))
    base = NOW // H4 * H4
    assert find_gaps(bars, H4) == (BarGap(base + 2 * H4, base + 4 * H4, 2),)
