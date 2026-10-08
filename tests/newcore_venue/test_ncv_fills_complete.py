"""Cowork 6068372233 (S1 blocker class): a fills page cut exactly at the limit must never look complete.

OK on a fills read means COMPLETE: every page was read to a SHORT page (by the RAW row count Binance sent) or past
the window end; the evidence (ReadOutcome.detail) says how many pages and how many repeated rows were dropped by
trade id. A FULL last page with no way to continue (by order) or the paging bound is UNKNOWN. Limit 41 here; our
trade is row 41. Fake HTTP only, DUMMY keys, no network."""
import pytest

from newcore.ports import venue as P
from newcore.venue import testnet_venue as TV

from test_ncv_venue_reads import NOW_MS, T0, ok, trade, venue

LIMIT = 41
OURS = 41


@pytest.fixture(autouse=True)
def limit(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', LIMIT)


def rows(ids, **kw):
    return [trade(i, **kw) for i in ids]


def ids(out):
    return [int(f.trade_id) for f in out.value]


# ---------------------------------------------------------------------------------------------- by time window
@pytest.mark.parametrize('n', [40, 41, 42])
def test_window_exactly_at_the_limit_pages_on_and_keeps_our_trade(n):
    first = rows(range(1, min(n, LIMIT) + 1))
    rest = rows(range(LIMIT + 1, n + 1))
    script = [ok(first)] + ([ok(rest)] if n >= LIMIT else [])
    v, http = venue(*script)
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.OK and ids(out) == list(range(1, n + 1))
    assert (OURS in ids(out)) == (n >= OURS)
    assert len(http.requests) == (1 if n < LIMIT else 2)          # a FULL page is never taken as the last one
    assert out.detail == f'complete pages={len(http.requests)} dups=0'


def test_window_a_full_page_whose_last_row_is_a_repeat_still_pages_on():
    page = rows(range(1, LIMIT)) + [trade(LIMIT - 1)]              # 41 rows sent, 40 distinct: FULL by the raw count
    v, http = venue(ok(page), ok([trade(OURS)]))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert len(http.requests) == 2 and out.kind is P.ReadKind.OK and OURS in ids(out)
    assert ids(out) == list(range(1, OURS + 1)) and out.detail == 'complete pages=2 dups=1'


def test_window_an_identical_overlap_between_pages_is_deduped_and_counted():
    v, http = venue(ok(rows(range(1, LIMIT + 1))), ok([trade(LIMIT), trade(LIMIT + 1)]))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.OK and ids(out) == list(range(1, LIMIT + 2)) and out.detail.endswith('dups=1')


def test_window_a_conflicting_overlap_is_unknown():
    v, _ = venue(ok(rows(range(1, LIMIT + 1))), ok([trade(LIMIT, qty='9'), trade(LIMIT + 1)]))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'conflicting_trade' and out.value is None


def test_window_full_pages_up_to_the_paging_bound_are_unknown(monkeypatch):
    monkeypatch.setattr(TV, 'FILL_WINDOW_PAGES', 2)
    v, http = venue(ok(rows(range(1, LIMIT + 1))), ok(rows(range(LIMIT + 1, 2 * LIMIT + 1))))
    out = v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'paging_bound' and len(http.requests) == 2


# ---------------------------------------------------------------------------------------------- by order
@pytest.mark.parametrize('n', [40, 41, 42])
def test_by_order_a_full_page_is_unknown_never_complete(n):
    v, _ = venue(ok(rows(range(1, n + 1))))
    out = v.fills('SOLUSDT', '5000')
    if n < LIMIT:
        assert out.kind is P.ReadKind.OK and ids(out) == list(range(1, n + 1)) and out.detail == 'complete pages=1 dups=0'
    else:
        assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'fills_truncated'      # row 41 (ours) may be cut


def test_by_order_a_full_page_with_a_repeated_last_row_is_still_full():
    v, _ = venue(ok(rows(range(1, LIMIT)) + [trade(LIMIT - 1)]))    # 41 sent, 40 distinct
    out = v.fills('SOLUSDT', '5000')
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'fills_truncated'


def test_by_order_an_identical_repeat_in_a_short_page_is_deduped_and_counted():
    v, _ = venue(ok([trade(1), trade(2), trade(2)]))
    out = v.fills('SOLUSDT', '5000')
    assert out.kind is P.ReadKind.OK and ids(out) == [1, 2] and out.detail == 'complete pages=1 dups=1'


def test_by_order_a_conflicting_repeat_is_unknown():
    v, _ = venue(ok([trade(1), trade(2), trade(2, qty='9')]))
    out = v.fills('SOLUSDT', '5000')
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'malformed'


# ---------------------------------------------------------------------------------------------- Codex 6069281718
# The window walk used a row past the window end as proof that the window was complete - assuming time is monotonic
# with the trade id. Now: complete only at a raw-SHORT page; time never goes back within or across continuation pages.
def READ(v):
    return v.fills('SOLUSDT', start_ms=T0, end_ms=NOW_MS)


def _codex_pages(window_end):
    """Codex's repro, page limit 2: page 1 = (id 1, w0 + 1), (id 2, w1 + 1) - a FULL raw page whose last row is past
    the window end; page 2 (fromId 3) = (id 3, w0 + 2) - an unseen in-window trade with an EARLIER time."""
    return ok([trade(1, t=T0 + 1), trade(2, t=window_end + 1)]), ok([trade(3, t=T0 + 2)])


def test_codex_repro_a_row_past_the_end_is_no_proof_and_the_time_regression_is_unknown(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', 2)
    v, http = venue(*_codex_pages(NOW_MS))
    out = READ(v)
    assert len(http.requests) == 2                                   # it did NOT stop after the first page
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'time_regression' and out.value is None


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
