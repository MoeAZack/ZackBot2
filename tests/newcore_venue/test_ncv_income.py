"""NC-03 S5: /fapi/v1/income parsing, time-window pagination and the funding / fee accounting read."""
import json
from decimal import Decimal as D

import pytest

from newcore.venue import records as R
from newcore.venue.income import (DAY_MS, DEFAULT_WINDOW_MS, commission_mismatches, income_history,
                                  summarize_income)
from newcore.venue.outcomes import ReadKind, ReadOutcome
from newcore.venue.transport import VenueInputError
from newcore.venue.wire import WireTimeout

from ncv_support import fixture, make, query_pairs, raw

T0 = 1759276800000          # synthetic window start


def test_parse_income_fixture():
    rows = R.parse_income(json.loads(fixture('income_mixed').body))
    assert len(rows) == 6
    c = rows[0]
    assert (c.symbol, c.income_type, c.income, c.asset, c.trade_id, c.tran_id) == \
        ('SOLUSDT', 'COMMISSION', D('-0.55575'), 'USDT', '698759', 9689322392)
    assert rows[2].trade_id is None and rows[5].symbol is None and type(rows[3].income) is D


@pytest.mark.parametrize('patch', [{'income': 1.5}, {'income': 'NaN'}, {'tranId': True}, {'incomeType': 'bad type'},
                                   {'time': 0}, {'tradeId': 1.5}, {'asset': ''}, {'symbol': 5},
                                   {'incomeType': 'COMMISSION\n'}, {'income': '-0.5\n'}, {'tranId': '9\n'}])
def test_parse_income_strict(patch):
    row = json.loads(fixture('income_mixed').body)[0]
    row.update(patch)
    with pytest.raises(R.MalformedResponse):
        R.parse_income([row])


def test_parse_income_duplicate_refused():
    row = json.loads(fixture('income_mixed').body)[0]
    with pytest.raises(R.MalformedResponse):
        R.parse_income([row, dict(row)])


def test_unknown_income_type_is_kept_not_rejected():
    row = dict(json.loads(fixture('income_mixed').body)[0], incomeType='SOME_NEW_TYPE')
    assert R.parse_income([row])[0].income_type == 'SOME_NEW_TYPE'


def test_income_request_shape():
    t, http = make('income_mixed')
    out = t.income(symbol='SOLUSDT', income_type='FUNDING_FEE', start_ms=T0, end_ms=T0 + DAY_MS, limit=1000)
    assert out.kind is ReadKind.OK and http.last.url.endswith('/fapi/v1/income') and http.last.method == 'GET'
    assert query_pairs(http.last)[:5] == [('symbol', 'SOLUSDT'), ('incomeType', 'FUNDING_FEE'),
                                          ('startTime', str(T0)), ('endTime', str(T0 + DAY_MS)), ('limit', '1000')]
    assert http.last.signed and out.rate.weight('1m') == 60


@pytest.mark.parametrize('kw', [dict(income_type='funding'), dict(limit=1001), dict(start_ms=5, end_ms=4),
                                dict(income_type=''), dict(symbol='sol')])
def test_income_input_validation(kw):
    t, http = make()
    with pytest.raises(VenueInputError):
        t.income(**kw)
    assert http.requests == []


def test_income_unknown_is_not_empty():
    t, _ = make(WireTimeout())
    out = t.income()
    assert out.kind is ReadKind.UNKNOWN and out.value is None


# ---------- accounting read ----------

def test_summary_funding_fee_pnl():
    s = summarize_income(R.parse_income(json.loads(fixture('income_mixed').body)))
    assert s.commission() == D('-1.1115') and s.commission(symbol='BTCUSDT') == 0
    assert s.funding() == D('-0.06855') and s.funding(symbol='SOLUSDT') == D('-0.11055')
    assert s.funding(symbol='BTCUSDT') == D('0.042') and s.realized_pnl() == D('-12.5')
    assert s.other_types() == ['TRANSFER'] and s.total('TRANSFER') == D('100') and s.rows == 6


def test_commission_matches_fills():
    income = R.parse_income(json.loads(fixture('income_mixed').body))
    fills = R.parse_fills(json.loads(fixture('user_trades').body))
    assert commission_mismatches(income, fills) == []


def test_commission_mismatches_detected():
    income = list(R.parse_income(json.loads(fixture('income_mixed').body)))
    fills = R.parse_fills(json.loads(fixture('user_trades').body))
    bad = income[0].__class__(**{**income[0].__dict__, 'income': D('-0.55')})
    out = commission_mismatches([bad] + income[1:], fills)
    assert out == [('698759', 'amount_mismatch', D('-0.55'), D('0.55575'))]
    out = commission_mismatches(income[1:], fills)
    assert out == [('698759', 'fill_without_commission_row', None, D('0.55575'))]
    out = commission_mismatches(income, fills[:1])
    assert out == [('698760', 'commission_row_without_fill', D('-0.55575'), None)]


# ---------- pagination ----------

class FakeIncomeVenue:
    """Answers income() like Binance: rows inside [start, end], ascending by time, first `limit` of them."""

    def __init__(self, rows, fail_at=None):
        self.rows = sorted(rows, key=lambda r: (r.time_ms, r.tran_id))
        self.calls, self.fail_at = [], dict(fail_at or {})

    def income(self, *, symbol=None, income_type=None, start_ms=None, end_ms=None, limit=None):
        self.calls.append((start_ms, end_ms, limit))
        n = len(self.calls)
        if n in self.fail_at:
            return self.fail_at[n]
        page = [r for r in self.rows if start_ms <= r.time_ms <= end_ms
                and (income_type is None or r.income_type == income_type)][:limit]
        return ReadOutcome(ReadKind.OK, 'income', value=tuple(page))


def row(i, t, itype='FUNDING_FEE'):
    return R.IncomeRow('SOLUSDT', itype, D('-0.01'), 'USDT', itype, t, 1000 + i, None)


def test_pagination_collects_every_row_once_across_windows():
    rows = [row(i, T0 + i * 3600 * 1000) for i in range(24 * 20)]          # hourly for 20 days
    v = FakeIncomeVenue(rows)
    out = income_history(v, start_ms=T0, end_ms=T0 + 20 * DAY_MS, page_limit=50)
    assert out.kind is ReadKind.OK and [r.tran_id for r in out.value] == [r.tran_id for r in rows]
    assert all(e - s + 1 <= DEFAULT_WINDOW_MS for s, e, _ in v.calls)
    assert v.calls[0][0] == T0 and max(e for _, e, _ in v.calls) == T0 + 20 * DAY_MS


def test_pagination_handles_ties_across_a_page_boundary():
    rows = [row(i, T0 + 1000) for i in range(4)] + [row(10 + i, T0 + 2000) for i in range(4)]
    out = income_history(FakeIncomeVenue(rows), start_ms=T0, end_ms=T0 + DAY_MS, page_limit=5)
    assert out.kind is ReadKind.OK and len(out.value) == 8 and len({r.key for r in out.value}) == 8


def test_rows_on_window_boundaries_are_not_skipped():
    w = DEFAULT_WINDOW_MS
    rows = [row(1, T0), row(2, T0 + w - 1), row(3, T0 + w), row(4, T0 + 2 * w), row(5, T0 + 2 * w + 1)]
    v = FakeIncomeVenue(rows)
    out = income_history(v, start_ms=T0, end_ms=T0 + 2 * w + 1, page_limit=5)
    assert out.kind is ReadKind.OK and [r.tran_id for r in out.value] == [1001, 1002, 1003, 1004, 1005]
    assert [s for s, _, _ in v.calls] == [T0, T0 + w, T0 + 2 * w]          # windows are contiguous, no gap


def test_full_page_inside_the_starting_millisecond_stops_at_once():
    rows = [row(i, T0 + 1000) for i in range(7)]
    v = FakeIncomeVenue(rows)
    out = income_history(v, start_ms=T0 + 1000, end_ms=T0 + DAY_MS, page_limit=5)
    assert out.unknown_reason == 'pagination_stuck' and len(v.calls) == 1


def test_more_than_a_page_in_one_millisecond_is_unknown():
    rows = [row(i, T0 + 1000) for i in range(7)]
    out = income_history(FakeIncomeVenue(rows), start_ms=T0, end_ms=T0 + DAY_MS, page_limit=5)
    assert out.kind is ReadKind.UNKNOWN and out.value is None and out.unknown_reason == 'pagination_stuck'


def test_failed_page_makes_the_whole_history_not_ok():
    rows = [row(i, T0 + i * 1000) for i in range(30)]
    unknown = ReadOutcome(ReadKind.UNKNOWN, 'income', unknown_reason='timeout')
    out = income_history(FakeIncomeVenue(rows, {3: unknown}), start_ms=T0, end_ms=T0 + DAY_MS, page_limit=5)
    assert out.kind is ReadKind.UNKNOWN and out.value is None and out.unknown_reason == 'timeout'
    t, _ = make('err_timestamp')
    out = income_history(t, start_ms=T0, end_ms=T0 + DAY_MS)
    assert out.kind is ReadKind.REJECTED and out.error.code == -1021 and out.value is None


def test_row_outside_window_or_wrong_type_is_unknown():
    bad = ReadOutcome(ReadKind.OK, 'income', value=(row(1, T0 - 1),))
    out = income_history(FakeIncomeVenue([], {1: bad}), start_ms=T0, end_ms=T0 + DAY_MS)
    assert out.unknown_reason == 'row_outside_window'
    wrong = ReadOutcome(ReadKind.OK, 'income', value=(row(1, T0 + 5, 'COMMISSION'),))
    out = income_history(FakeIncomeVenue([], {1: wrong}), start_ms=T0, end_ms=T0 + DAY_MS, income_type='FUNDING_FEE')
    assert out.unknown_reason == 'unrequested_income_type'
    unordered = ReadOutcome(ReadKind.OK, 'income', value=(row(1, T0 + 9), row(2, T0 + 5)))
    out = income_history(FakeIncomeVenue([], {1: unordered}), start_ms=T0, end_ms=T0 + DAY_MS)
    assert out.unknown_reason == 'page_not_ascending'


def test_request_budget_is_bounded():
    rows = [row(i, T0 + i * 1000) for i in range(100)]
    out = income_history(FakeIncomeVenue(rows), start_ms=T0, end_ms=T0 + DAY_MS, page_limit=5, max_requests=3)
    assert out.kind is ReadKind.UNKNOWN and out.unknown_reason == 'too_many_pages'


def test_empty_range_is_ok_empty():
    out = income_history(FakeIncomeVenue([]), start_ms=T0, end_ms=T0 + 3 * DAY_MS)
    assert out.kind is ReadKind.OK and out.value == ()


def test_pagination_through_transport_request_params():
    page = json.loads(fixture('income_mixed').body)[:2]
    t, http = make(raw(200, json.dumps(page).encode()))
    out = income_history(t, start_ms=1759917000000, end_ms=1759917000999, income_type='COMMISSION', page_limit=1000)
    assert out.kind is ReadKind.OK and len(out.value) == 2
    q = dict(query_pairs(http.last))
    assert (q['incomeType'], q['startTime'], q['endTime'], q['limit']) == \
        ('COMMISSION', '1759917000000', '1759917000999', '1000')


@pytest.mark.parametrize('kw', [dict(start_ms=0), dict(end_ms=T0 - 1), dict(page_limit=0), dict(window_ms=8 * DAY_MS),
                                dict(max_requests=0), dict(start_ms=1.5)])
def test_history_argument_validation(kw):
    args = dict(start_ms=T0, end_ms=T0 + DAY_MS)
    args.update(kw)
    with pytest.raises(ValueError):
        income_history(FakeIncomeVenue([]), **args)


# First testnet smoke (owner, e524715): the testnet faucet credit is a TRANSFER row with tranId 0, and an account can
# have several. Shape copied from the sanitized smoke cassette.
FAUCET = {'symbol': '', 'incomeType': 'TRANSFER', 'income': '5000.00000000', 'asset': 'USDT', 'time': 1791079456000,
          'info': 'TRANSFER', 'tranId': 0, 'tradeId': ''}


def test_testnet_faucet_transfers_with_tran_id_zero_parse():
    second = dict(FAUCET, time=FAUCET['time'] + 60_000)
    rows = R.parse_income([FAUCET, second, json.loads(fixture('income_mixed').body)[0]])
    assert [(r.income_type, r.tran_id, r.symbol) for r in rows[:2]] == [('TRANSFER', 0, None)] * 2
    with pytest.raises(R.MalformedResponse):
        R.parse_income([FAUCET, dict(FAUCET)])                        # the very same row twice is still a duplicate


@pytest.mark.parametrize('field, value', [('info', 'TRANSFER_2'), ('symbol', 'XAUUSDT'), ('tradeId', '77')])
def test_tran_id_zero_key_uses_the_full_stable_row(field, value):
    # Codex 6076485539: same millisecond, same amount, one other stable field differs -> two rows, not a duplicate
    rows = R.parse_income([FAUCET, dict(FAUCET, **{field: value})])
    assert len(rows) == 2 and rows[0].key != rows[1].key


@pytest.mark.parametrize('itype', ['COMMISSION', 'REALIZED_PNL', 'FUNDING_FEE'])
def test_tran_id_zero_stays_refused_outside_transfers(itype):
    row = json.loads(fixture('income_mixed').body)[0]
    row.update(incomeType=itype, tranId=0)
    with pytest.raises(R.MalformedResponse):
        R.parse_income([row])
