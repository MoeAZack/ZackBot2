"""TestnetVenue.trades(symbol, side, from_ms): the read S1's ownership / provenance checks use (FakeVenue.trades shape:
port VenueFill rows of one (symbol, position side) since from_ms inclusive, oldest first). Same completeness rules as
fills by window (Cowork 6068372233): OK = complete; full page by raw rows; dedupe by trade id, a conflicting repeat
UNKNOWN; bounded pages. Limit 41 where paging matters; our trade is row 41. Fake HTTP only, DUMMY keys, no network."""
import pytest

from newcore.ports import venue as P
from newcore.ports.values import PortValueError
from newcore.venue import testnet_venue as TV

from ncv_support import query_pairs, raw
from test_ncv_venue_reads import NOW_MS, T0, ok, trade, venue

LIMIT = 41
OURS = 41
DAY = 24 * 3600 * 1000


def rows(ids, **kw):
    return [trade(i, **kw) for i in ids]


def ids(out):
    return [int(f.trade_id) for f in out.value]


# ---------------------------------------------------------------------------------------------- shape
def test_one_side_as_port_venue_fills_oldest_first_from_the_window_start_inclusive():
    v, http = venue(ok([trade(1, t=T0), trade(2, ps='SHORT', side='SELL', t=T0 + 5), trade(3, side='SELL', t=T0 + 9),
                        trade(4, t=T0 + 20)]))
    out = v.trades('SOLUSDT', 'LONG', T0)
    assert out.kind is P.ReadKind.OK and ids(out) == [1, 3, 4] and out.detail == 'complete pages=1 dups=0'
    assert all(type(f) is P.VenueFill and f.position_side == 'LONG' and f.symbol == 'SOLUSDT' for f in out.value)
    assert out.value[0].at_ms == T0 and out.observed_at_ms == NOW_MS               # from_ms is inclusive
    q = dict(query_pairs(http.requests[0]))
    assert (q['symbol'], q['startTime'], q['endTime']) == ('SOLUSDT', str(T0), str(NOW_MS))
    short = venue(ok([trade(2, ps='SHORT', side='SELL', t=T0 + 5)]))[0].trades('SOLUSDT', 'SHORT', T0)
    assert ids(short) == [2] and short.value[0].position_side == 'SHORT'


def test_a_one_way_row_is_unknown():
    v, _ = venue(ok([trade(1, ps='BOTH')]))
    out = v.trades('SOLUSDT', 'LONG', T0)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'one_way_row' and out.value is None


def test_a_start_in_the_future_is_a_trusted_empty_without_a_request():
    v, http = venue()
    out = v.trades('SOLUSDT', 'LONG', NOW_MS + 1)
    assert out.kind is P.ReadKind.OK and out.value == () and http.requests == []


def test_a_long_range_walks_seven_day_windows():
    v, http = venue(ok([]), ok([]), ok([trade(9, t=NOW_MS - 10)]))
    out = v.trades('SOLUSDT', 'LONG', NOW_MS - 15 * DAY)
    assert out.kind is P.ReadKind.OK and ids(out) == [9] and len(http.requests) == 3
    assert out.detail == 'complete pages=3 dups=0'


@pytest.mark.parametrize('side, from_ms', [('BOTH', T0), ('long', T0), ('LONG', 0), ('LONG', True), ('LONG', '1')])
def test_arguments_are_checked(side, from_ms):
    v, _ = venue()
    with pytest.raises(PortValueError):
        v.trades('SOLUSDT', side, from_ms)


# ---------------------------------------------------------------------------------------------- completeness
@pytest.fixture
def limit41(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', LIMIT)


@pytest.mark.parametrize('n', [40, 41, 42])
def test_exactly_at_the_limit_pages_on_and_keeps_our_trade(limit41, n):
    first = rows(range(1, min(n, LIMIT) + 1))
    rest = rows(range(LIMIT + 1, n + 1))
    v, http = venue(*([ok(first)] + ([ok(rest)] if n >= LIMIT else [])))
    out = v.trades('SOLUSDT', 'LONG', T0)
    assert out.kind is P.ReadKind.OK and ids(out) == list(range(1, n + 1)) and (OURS in ids(out)) == (n >= OURS)
    assert len(http.requests) == (1 if n < LIMIT else 2)
    second = dict(query_pairs(http.requests[-1]))
    assert n < LIMIT or (second['fromId'] == str(LIMIT + 1) and 'startTime' not in second)


def test_a_full_page_whose_last_row_is_a_repeat_still_pages_on(limit41):
    v, http = venue(ok(rows(range(1, LIMIT)) + [trade(LIMIT - 1)]), ok([trade(OURS)]))
    out = v.trades('SOLUSDT', 'LONG', T0)
    assert len(http.requests) == 2 and ids(out) == list(range(1, OURS + 1)) and out.detail == 'complete pages=2 dups=1'


def test_an_identical_overlap_is_deduped_and_a_conflicting_one_is_unknown(limit41):
    v, _ = venue(ok(rows(range(1, LIMIT + 1))), ok([trade(LIMIT), trade(LIMIT + 1)]))
    out = v.trades('SOLUSDT', 'LONG', T0)
    assert out.kind is P.ReadKind.OK and ids(out) == list(range(1, LIMIT + 2)) and out.detail.endswith('dups=1')
    v, _ = venue(ok(rows(range(1, LIMIT + 1))), ok([trade(LIMIT, qty='9'), trade(LIMIT + 1)]))
    out = v.trades('SOLUSDT', 'LONG', T0)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'conflicting_trade'


def test_a_conflicting_repeat_inside_a_page_is_unknown():
    v, _ = venue(ok([trade(1), trade(1, qty='9')]))
    out = v.trades('SOLUSDT', 'LONG', T0)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'malformed'


def test_full_pages_up_to_the_bound_are_unknown(limit41, monkeypatch):
    monkeypatch.setattr(TV, 'TRADES_MAX_PAGES', 2)
    v, http = venue(ok(rows(range(1, LIMIT + 1))), ok(rows(range(LIMIT + 1, 2 * LIMIT + 1))))
    out = v.trades('SOLUSDT', 'LONG', T0)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'paging_bound' and len(http.requests) == 2


def test_an_error_mid_paging_is_unknown_never_a_partial_list(limit41):
    v, _ = venue(ok(rows(range(1, LIMIT + 1))), raw(503, b'busy'))
    out = v.trades('SOLUSDT', 'LONG', T0)
    assert out.kind is P.ReadKind.UNKNOWN and out.value is None


def test_a_rejected_page_is_passed_through():
    v, _ = venue(raw(400, b'{"code": -1121, "msg": "Invalid symbol."}'))
    out = v.trades('SOLUSDT', 'LONG', T0)
    assert out.kind is P.ReadKind.REJECTED and out.error_code == -1121
