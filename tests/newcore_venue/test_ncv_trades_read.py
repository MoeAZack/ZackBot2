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


# ---------------------------------------------------------------------------------------------- Codex 6069281718
# The window walk used a row past the window end as proof that the window was complete - assuming time is monotonic
# with the trade id. Now: complete only at a raw-SHORT page; time never goes back within or across continuation pages.
def READ(v):
    return v.trades('SOLUSDT', 'LONG', T0)


def _codex_pages(window_end):
    """Codex's repro, page limit 2: page 1 = (id 1, w0 + 1), (id 2, w1 + 1) - a FULL raw page whose last row is past
    the window end; page 2 (fromId 3) = (id 3, w0 + 2) - an unseen in-window trade with an EARLIER time."""
    return ok([trade(1, t=T0 + 1), trade(2, t=window_end + 1)]), ok([trade(3, t=T0 + 2)])


def test_codex_repro_a_row_past_the_end_is_no_proof(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', 2)
    v, http = venue(*_codex_pages(NOW_MS))
    out = READ(v)                                     # (6069415268: the past-end row on the BOUNDED page
    assert out.kind is P.ReadKind.UNKNOWN and out.value is None   # already stops it, never a false complete)
    assert out.detail == 'window_mismatch' and len(http.requests) == 1


def test_past_end_rows_on_continuation_pages_then_a_time_regression_is_unknown(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', 2)                  # 6069281718 on fromId pages
    v, http = venue(ok([trade(1, t=T0 + 1), trade(2, t=T0 + 2)]), ok([trade(3, t=NOW_MS + 1), trade(4, t=NOW_MS + 2)]),
                    ok([trade(5, t=T0 + 3)]))
    out = READ(v)
    assert len(http.requests) == 3                                   # a past-end row did not stop the walk
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'time_regression'


def test_a_time_regression_across_continuation_pages_is_unknown(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', 2)
    v, _ = venue(ok([trade(1, t=T0 + 10), trade(2, t=T0 + 20)]), ok([trade(3, t=T0 + 15), trade(4, t=T0 + 30)]))
    out = READ(v)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'time_regression'


def test_a_time_regression_inside_a_page_is_unknown():
    v, _ = venue(ok([trade(1, t=T0 + 20), trade(2, t=T0 + 10)]))
    out = READ(v)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'time_regression'


def test_equal_times_and_rows_past_the_end_are_fine_when_paged_to_a_short_page(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', 2)
    v, http = venue(ok([trade(1, t=T0 + 5), trade(2, t=T0 + 5)]), ok([trade(3, t=NOW_MS + 1), trade(4, t=NOW_MS + 2)]),
                    ok([]))
    out = READ(v)
    assert out.kind is P.ReadKind.OK and [int(f.trade_id) for f in out.value] == [1, 2] and len(http.requests) == 3


# ---------------------------------------------------------------------------------------------- Codex 6069415268
# The first page of each window is BOUNDED (startTime / endTime): a row outside those bounds is a malformed answer,
# never filtered away - else non-empty evidence became a trusted EMPTY history. fromId pages cannot be bounded, so a
# past-end row there stays valid (the continuation tests above).
@pytest.mark.parametrize('when', ['before_start', 'after_end'])
def test_a_bounded_short_page_with_a_row_outside_its_window_is_unknown_never_empty(when):
    t = T0 - 1 if when == 'before_start' else NOW_MS + 1
    v, http = venue(ok([trade(1, t=t)]))
    out = READ(v)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'window_mismatch' and out.value is None
    assert len(http.requests) == 1


def test_a_bounded_page_mixing_in_window_and_outside_rows_is_unknown():
    v, _ = venue(ok([trade(1, t=T0 + 1), trade(2, t=NOW_MS + 1)]))
    out = READ(v)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'window_mismatch'
