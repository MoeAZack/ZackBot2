"""FakeVenue: next-open market fills, the zb-path/1 stop walk, gaps, costs, reduce-only, ids, fault hooks."""
from decimal import Decimal as D

import pytest

from newcore.adapters.fake_venue import CostModel, FakeVenue, path_points
from newcore.ports import MarketOrder, OrderRef, OutcomeKind, ReadKind, StopOrder, VenuePort, client_id_for
from slice_helpers import H4, T0, flat_bars

SYM = 'SOLUSDT'


def cid(n, route='classic'):
    return client_id_for('int_' + format(n, '032x'), route)


def ref(n):
    return OrderRef(symbol=SYM, client_id=cid(n))


def venue(overrides=None, n=10, **kw):
    return FakeVenue({SYM: flat_bars(n, overrides=overrides)}, H4, equity=D('500'), **kw)


def buy_long(v, n, qty='5'):
    return v.submit_market(MarketOrder(ref=ref(n), position_side='LONG', qty=D(qty), reduce=False))


def test_satisfies_the_port():
    assert isinstance(venue(), VenuePort)


def test_zb_path_1():
    assert path_points(1, 3, 0, 2, 'LONG') == (1, 0, 3, 2)          # green: open low high close
    assert path_points(2, 3, 0, 1, 'SHORT') == (2, 3, 0, 1)         # red: open high low close
    assert path_points(1, 3, 0, 1, 'LONG') == (1, 3, 0, 1)          # doji, long: adverse (low) last
    assert path_points(1, 3, 0, 1, 'SHORT') == (1, 0, 3, 1)         # doji, short: adverse (high) last


def test_market_fills_at_the_next_open_with_slippage_and_fee():
    v = venue()
    v.advance_to(T0 + 2 * H4)
    out = buy_long(v, 1)
    assert out.kind is OutcomeKind.FINAL and out.status == 'FILLED'
    assert out.executed_qty == D('5') and out.avg_price == D('100.02')
    f, = v.fills(SYM, out.exchange_order_id).value
    assert f.fee == D('5') * D('100.02') * D('0.0005') and f.at_ms == T0 + 2 * H4
    assert v.equity().value == (D('500') - f.fee,)
    pos = {(p.symbol, p.side): p for p in v.positions().value}
    assert pos[(SYM, 'LONG')].qty == D('5') and pos[(SYM, 'SHORT')].qty == 0


def test_stop_inside_the_path_fills_at_the_stop_with_slippage():
    v = venue({3: ('100', '100.2', '97.5', '97.8')})                    # golden G-STOP-L-01 candle
    v.advance_to(T0 + H4)
    buy_long(v, 1)
    st = v.submit_stop(StopOrder(ref=ref(2), position_side='LONG', qty=D('5'), stop_price=D('98.02')))
    assert st.kind is OutcomeKind.KNOWN and st.status == 'NEW'
    assert [o.ref.client_id for o in v.open_orders().value] == [cid(2)]
    v.advance_to(T0 + 3 * H4)
    assert v.query(ref(2)).kind is OutcomeKind.KNOWN                  # candles 1, 2 never reach it
    v.advance_to(T0 + 4 * H4)
    q = v.query(ref(2))
    assert q.kind is OutcomeKind.FINAL and q.status == 'FILLED' and q.avg_price == D('98.02') * D('0.9998')
    assert v.open_orders().value == ()
    assert all(p.qty == 0 for p in v.positions().value)
    f, = v.fills(SYM, q.exchange_order_id).value
    assert f.at_ms == T0 + 3 * H4 and f.realized_pnl == (D('98.000396') - D('100.02')) * 5


def test_gapped_stop_fills_at_the_open():
    v = venue({3: ('95', '95.2', '94.8', '95')})                         # golden G-GAP-STOP-L-01 candle
    v.advance_to(T0 + H4)
    buy_long(v, 1)
    v.submit_stop(StopOrder(ref=ref(2), position_side='LONG', qty=D('5'), stop_price=D('98.02')))
    v.advance_to(T0 + 4 * H4)
    assert v.query(ref(2)).avg_price == D('95') * D('0.9998')


def test_short_stop_buys_back_above():
    v = venue({3: ('100', '102.5', '99.8', '102.2')})                    # golden G-STOP-S-01 candle
    v.advance_to(T0 + H4)
    out = v.submit_market(MarketOrder(ref=ref(1), position_side='SHORT', qty=D('5'), reduce=False))
    assert out.avg_price == D('99.98')
    v.submit_stop(StopOrder(ref=ref(2), position_side='SHORT', qty=D('5'), stop_price=D('101.98')))
    v.advance_to(T0 + 4 * H4)
    assert v.query(ref(2)).avg_price == D('101.98') * D('1.0002')


def test_funding_is_charged_per_candle_on_open_positions():
    v = FakeVenue({SYM: flat_bars(6)}, H4, equity=D('500'), costs=CostModel(funding_per_bar=D('0.00005')))
    v.advance_to(T0 + H4)
    buy_long(v, 1)
    v.advance_to(T0 + 3 * H4)
    rows = v.funding(SYM, 'LONG', 0, T0 + 10 * H4).value
    assert [r.at_ms for r in rows] == [T0 + 2 * H4, T0 + 3 * H4]
    assert all(r.amount == D('5') * D('100') * D('0.00005') for r in rows)


def test_reduce_only_never_adds_and_ids_are_never_reused():
    v = venue()
    v.advance_to(T0 + H4)
    buy_long(v, 1)
    too_big = v.submit_market(MarketOrder(ref=ref(2), position_side='LONG', qty=D('6'), reduce=True))
    assert too_big.kind is OutcomeKind.REJECTED and too_big.error_code == -2022
    assert buy_long(v, 1).kind is OutcomeKind.REJECTED                  # same client id: never a second fill
    no_pos = v.submit_stop(StopOrder(ref=ref(3), position_side='SHORT', qty=D('1'), stop_price=D('110')))
    assert no_pos.kind is OutcomeKind.REJECTED
    trig = v.submit_stop(StopOrder(ref=ref(4), position_side='LONG', qty=D('5'), stop_price=D('100.5')))
    assert trig.kind is OutcomeKind.REJECTED and trig.error_code == -2021
    assert len(v.orders_submitted()) == 1


def test_cancel_query_not_found_and_reads():
    v = venue()
    v.advance_to(T0 + H4)
    buy_long(v, 1)
    v.submit_stop(StopOrder(ref=ref(2), position_side='LONG', qty=D('5'), stop_price=D('98')))
    c = v.cancel(ref(2))
    assert c.kind is OutcomeKind.FINAL and c.status == 'CANCELED' and c.executed_qty == 0
    assert v.cancel(ref(2)).kind is OutcomeKind.REJECTED                 # no longer open: unchanged
    assert v.query(ref(9)).kind is OutcomeKind.NOT_FOUND
    assert v.cancel(ref(9)).kind is OutcomeKind.NOT_FOUND


def test_end_of_data_market_is_rejected():
    v = venue(n=3)
    v.advance_to(T0 + 3 * H4)
    assert buy_long(v, 1).kind is OutcomeKind.REJECTED


def test_fault_hooks_default_off_and_work():
    v = venue()
    v.advance_to(T0 + H4)
    v.lose_next_market_answer('filled')
    out = buy_long(v, 1)
    assert out.kind is OutcomeKind.UNKNOWN and out.executed_qty is None
    assert v.query(ref(1)).kind is OutcomeKind.FINAL                     # it did execute
    v.lose_next_market_answer('not_filled')
    assert buy_long(v, 2).kind is OutcomeKind.UNKNOWN
    assert v.query(ref(2)).kind is OutcomeKind.NOT_FOUND                 # nothing was created
    v.not_found(cid(1), times=1)
    assert v.query(ref(1)).kind is OutcomeKind.NOT_FOUND
    assert v.query(ref(1)).kind is OutcomeKind.FINAL
    v.unknown_reads(1)
    r = v.positions()
    assert r.kind is ReadKind.UNKNOWN and r.value is None                # unknown is never empty
    assert v.positions().kind is ReadKind.OK


def test_clock_only_moves_forward_on_boundaries():
    v = venue()
    v.advance_to(T0 + H4)
    with pytest.raises(ValueError):
        v.advance_to(T0)
    with pytest.raises(ValueError):
        v.advance_to(T0 + H4 + 1)
