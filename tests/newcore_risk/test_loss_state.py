"""RISK-LOSS-01 loss-state producer: Cairo day roll, peak, latches, cooldown
and candle-window bookkeeping, feeding the NC-06 gateway."""
from __future__ import annotations

import ast
import dataclasses
import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from newcore import loss_state as LS
from newcore.risk import ClusterEntry, LossLimits, LossState, PositionSide

D = Decimal
HOUR = 3600 * 1000
DAY = 24 * HOUR


def ms(text):
    return int(datetime.datetime.fromisoformat(text).timestamp() * 1000)


T0 = ms('2026-11-04T08:00:00+00:00')          # Cairo 10:00 on 4 Nov (UTC+2)


def limits(**changes):
    row = LossLimits(daily_loss_fraction=D('.03'), daily_loss_r=D('6'), risk_unit_fraction=D('.005'),
                     max_bucket_side_risk_r=D('2'), one_position_per_symbol_side=True,
                     max_drawdown_fraction=D('.1'), max_net_side_risk_r=D('4'),
                     max_entries_per_strategy_per_candle=2, max_entries_per_side_per_candle=3,
                     cooldown_stop_outs=3, cooldown_window_ms=DAY, cooldown_pause_ms=DAY)
    return dataclasses.replace(row, **changes)


def fresh(equity='10000', unrealized='0'):
    return LS.start('acct_' + 'a' * 32, now_ms=T0, equity=D(equity), unrealized_pnl=D(unrealized))


def snap(t, now, unrealized='0', strategies=('trend', 'vwaprev'), window=HOUR):
    return LS.snapshot(t, limits(), now_ms=now, unrealized_pnl=D(unrealized),
                       strategy_ids=strategies, cluster_window_ms=window)


def test_cairo_day_boundary_follows_local_midnight():
    assert LS.cairo_trading_day(ms('2026-11-04T21:59:59+00:00')) == '2026-11-04'
    assert LS.cairo_trading_day(ms('2026-11-04T22:00:00+00:00')) == '2026-11-05'
    assert LS.cairo_trading_day(ms('2026-07-01T20:59:59+00:00')) == '2026-07-01'   # summer UTC+3
    assert LS.cairo_trading_day(ms('2026-07-01T21:00:00+00:00')) == '2026-07-02'


def test_snapshot_is_valid_gateway_state_on_a_fresh_day():
    t = fresh()
    state = snap(t, T0)
    assert type(state) is LossState
    assert (state.trading_day, state.day_start_equity, state.realized_pnl, state.unrealized_pnl) == (
        '2026-11-04', D('10000'), D('0'), D('0'))
    assert not state.halt_latched and not state.drawdown_kill_latched


def test_realized_losses_latch_the_daily_halt_and_it_survives_recovery():
    t = fresh()
    t = LS.record_close(t, strategy_id='trend', realized_pnl=D('-300'), stopped_out=True, now_ms=T0 + 1)
    t = LS.observe(t, limits(), now_ms=T0 + 2, equity=D('9700'), unrealized_pnl=D('0'))
    assert t.halt_latched
    t = LS.record_close(t, strategy_id='trend', realized_pnl=D('500'), stopped_out=False, now_ms=T0 + 3)
    t = LS.observe(t, limits(), now_ms=T0 + 4, equity=D('10200'), unrealized_pnl=D('0'))
    assert t.halt_latched and snap(t, T0 + 4).halt_latched


def test_unrealized_pnl_is_measured_from_the_day_start_mark():
    # 200 already underwater at day start is yesterday's loss, not today's.
    t = fresh(unrealized='-200')
    t = LS.observe(t, limits(), now_ms=T0 + 1, equity=D('9900'), unrealized_pnl=D('-300'))
    assert snap(t, T0 + 1, unrealized='-300').unrealized_pnl == D('-100')
    assert not t.halt_latched
    t = LS.observe(t, limits(), now_ms=T0 + 2, equity=D('9700'), unrealized_pnl=D('-500'))
    assert t.halt_latched


def test_day_roll_resets_daily_state_but_not_the_drawdown_kill():
    t = fresh()
    t = LS.record_close(t, strategy_id='trend', realized_pnl=D('-1200'), stopped_out=True, now_ms=T0 + 1)
    t = LS.observe(t, limits(), now_ms=T0 + 2, equity=D('8800'), unrealized_pnl=D('0'))
    assert t.halt_latched and t.drawdown_kill_latched
    nxt = ms('2026-11-04T22:00:00+00:00')
    t = LS.observe(t, limits(), now_ms=nxt, equity=D('8800'), unrealized_pnl=D('0'))
    assert t.trading_day == '2026-11-05' and t.realized_pnl == 0 and not t.halt_latched
    assert t.day_start_equity == D('8800')
    assert t.drawdown_kill_latched                                   # needs the owner


def test_owner_resume_is_the_only_way_to_clear_the_kill():
    t = fresh()
    t = LS.observe(t, limits(), now_ms=T0 + 1, equity=D('9000'), unrealized_pnl=D('-1000'))
    assert t.drawdown_kill_latched
    t = LS.observe(t, limits(), now_ms=T0 + 2, equity=D('11000'), unrealized_pnl=D('1000'))
    assert t.drawdown_kill_latched
    t = LS.owner_resume(t, now_ms=T0 + 3, equity=D('11000'))
    assert not t.drawdown_kill_latched and t.peak_equity == D('11000')


def test_new_high_cannot_mask_a_drawdown_measured_before_it():
    t = fresh()
    t = LS.observe(t, limits(), now_ms=T0 + 1, equity=D('12000'), unrealized_pnl=D('2000'))
    assert t.peak_equity == D('12000') and not t.drawdown_kill_latched
    t = LS.observe(t, limits(), now_ms=T0 + 2, equity=D('10800'), unrealized_pnl=D('800'))
    assert t.drawdown_kill_latched                                   # exactly 10% off 12000


def test_three_stop_outs_in_a_day_pause_that_strategy_only():
    t = fresh()
    for i in range(3):
        t = LS.record_close(t, strategy_id='vwaprev', realized_pnl=D('-10'), stopped_out=True,
                            now_ms=T0 + i * HOUR + 1)
    now = T0 + 3 * HOUR
    t = LS.observe(t, limits(), now_ms=now, equity=D('9970'), unrealized_pnl=D('0'))
    assert snap(t, now).paused_strategies == ('vwaprev',)
    later = T0 + 2 * HOUR + 1 + DAY
    t = LS.observe(t, limits(), now_ms=later, equity=D('9970'), unrealized_pnl=D('0'))
    assert snap(t, later).paused_strategies == ()


def test_cluster_entries_are_only_the_current_window_in_canonical_order():
    assert T0 % HOUR == 0
    t = fresh()
    t = LS.record_entry(t, ClusterEntry(strategy_id='trend', symbol='BTCUSDT', side=PositionSide.LONG),
                        now_ms=T0 + HOUR - 1)                               # previous window
    for symbol in ('DOGEUSDT', 'BNBUSDT'):
        t = LS.record_entry(t, ClusterEntry(strategy_id='vwaprev', symbol=symbol, side=PositionSide.LONG),
                            now_ms=T0 + HOUR + 5)
    now = T0 + HOUR + 10
    t = LS.observe(t, limits(), now_ms=now, equity=D('10000'), unrealized_pnl=D('0'))
    assert [x.symbol for x in snap(t, now).cluster_entries] == ['BNBUSDT', 'DOGEUSDT']


@pytest.mark.parametrize('call', [
    lambda t: LS.observe(t, limits(), now_ms=T0 - 1, equity=D('1'), unrealized_pnl=D('0')),
    lambda t: LS.observe(t, limits(), now_ms=T0 + 1, equity=D('0'), unrealized_pnl=D('0')),
    lambda t: LS.observe(t, limits(), now_ms=T0 + 1, equity=10000, unrealized_pnl=D('0')),
    lambda t: LS.observe(t, limits(), now_ms=T0 + 1, equity=D('1'), unrealized_pnl=D('NaN')),
    lambda t: LS.observe(t, None, now_ms=T0 + 1, equity=D('1'), unrealized_pnl=D('0')),
    lambda t: LS.observe(t, limits(), now_ms=True, equity=D('1'), unrealized_pnl=D('0')),
    lambda t: LS.record_close(t, strategy_id='', realized_pnl=D('1'), stopped_out=True, now_ms=T0 + 1),
    lambda t: LS.record_close(t, strategy_id='x', realized_pnl=1, stopped_out=True, now_ms=T0 + 1),
    lambda t: LS.record_close(t, strategy_id='x', realized_pnl=D('1'), stopped_out=1, now_ms=T0 + 1),
    lambda t: LS.record_close(t, strategy_id='x', realized_pnl=D('1'), stopped_out=True, now_ms=T0 + DAY),
    lambda t: LS.record_entry(t, ('trend', 'BTCUSDT', PositionSide.LONG), now_ms=T0 + 1),
    lambda t: snap(t, T0 + 1),                                    # snapshot without observe
    lambda t: LS.snapshot(t, limits(), now_ms=T0, unrealized_pnl=D('0'), strategy_ids=['x'],
                          cluster_window_ms=HOUR),
    lambda t: LS.snapshot(t, limits(), now_ms=T0, unrealized_pnl=D('0'), strategy_ids=(),
                          cluster_window_ms=0),
    lambda t: LS.owner_resume(t, now_ms=T0 - 1, equity=D('1')),
    # Review 3:
    lambda t: LS.record_close(t, strategy_id=123, realized_pnl=D('1'), stopped_out=False, now_ms=T0 + 1),
    lambda t: LS.record_close(LS.record_close(t, strategy_id='x', realized_pnl=D('9E+999999'),
                                              stopped_out=False, now_ms=T0 + 1),
                              strategy_id='x', realized_pnl=D('9E+999999'), stopped_out=False, now_ms=T0 + 2),
    lambda t: LS.observe(fresh(unrealized='-9E+999999'), limits(), now_ms=T0 + 1, equity=D('1'),
                         unrealized_pnl=D('9E+999999')),
    lambda t: LS.record_entry(t, ClusterEntry(strategy_id='trend', symbol='', side=PositionSide.LONG),
                              now_ms=T0 + 1),
    lambda t: LS.snapshot(t, limits(), now_ms=T0, unrealized_pnl=D('0'), strategy_ids=(),
                          cluster_window_ms=7 * DAY),
    lambda t: LS.observe(t, limits(), now_ms=2 ** 63 - 1, equity=D('1'), unrealized_pnl=D('0')),
])
def test_malformed_input_raises_so_no_state_and_no_risk(call):
    with pytest.raises(ValueError):
        call(fresh())


def test_start_rejects_bad_equity():
    with pytest.raises(ValueError):
        LS.start('acct_x', now_ms=T0, equity=D('0'), unrealized_pnl=D('0'))


def test_import_boundary_is_stdlib_plus_nc06_only():
    tree = ast.parse((Path(__file__).parents[2] / 'newcore' / 'loss_state.py').read_text(encoding='utf-8'))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or '')
    assert {m for m in modules if m.startswith('newcore')} == {'newcore.risk'}
    assert not {m for m in modules if m.split('.')[0] in ('engine', 'requests', 'binance_client', 'time')}


def test_snapshot_feeds_the_gateway_helpers_consistently():
    from newcore.risk import daily_loss_breached, drawdown_kill_breached
    t = fresh()
    t = LS.record_close(t, strategy_id='trend', realized_pnl=D('-150'), stopped_out=True, now_ms=T0 + 1)
    t = LS.observe(t, limits(), now_ms=T0 + 2, equity=D('9700'), unrealized_pnl=D('-150'))
    state = snap(t, T0 + 2, unrealized='-150')
    assert daily_loss_breached(limits(), state) and state.halt_latched
    assert not drawdown_kill_breached(limits(), state, D('9700'))


def test_pnl_is_exact_beyond_28_digits():
    t = LS.record_close(fresh(), strategy_id='x', realized_pnl=D('1E+28'), stopped_out=False, now_ms=T0 + 1)
    t = LS.record_close(t, strategy_id='x', realized_pnl=D('-1.5'), stopped_out=False, now_ms=T0 + 2)
    assert t.realized_pnl == D('9999999999999999999999999998.5')


def test_resume_on_a_day_that_alone_lost_ten_percent_waits_for_the_day_roll():
    t = fresh()
    t = LS.observe(t, limits(), now_ms=T0 + 1, equity=D('9000'), unrealized_pnl=D('-1000'))
    t = LS.owner_resume(t, now_ms=T0 + 2, equity=D('9000'))
    t = LS.observe(t, limits(), now_ms=T0 + 3, equity=D('9000'), unrealized_pnl=D('-1000'))
    assert t.drawdown_kill_latched and t.halt_latched
    nxt = ms('2026-11-04T22:00:00+00:00')
    t = LS.owner_resume(LS.observe(t, limits(), now_ms=nxt, equity=D('9000'), unrealized_pnl=D('0')),
                        now_ms=nxt, equity=D('9000'))
    t = LS.observe(t, limits(), now_ms=nxt + 1, equity=D('9000'), unrealized_pnl=D('0'))
    assert not t.drawdown_kill_latched and not t.halt_latched


def test_day_roll_across_cairo_dst_end():
    # Egypt summer time ended 2026-10-29 (last Thursday of October): midnight
    # moves from 21:00Z to 22:00Z.
    assert LS.cairo_trading_day(ms('2026-10-28T20:59:59+00:00')) == '2026-10-28'
    assert LS.cairo_trading_day(ms('2026-10-28T21:00:00+00:00')) == '2026-10-29'
    assert LS.cairo_trading_day(ms('2026-10-30T21:59:59+00:00')) == '2026-10-30'
    assert LS.cairo_trading_day(ms('2026-10-30T22:00:00+00:00')) == '2026-10-31'


def test_position_open_across_midnight_is_not_double_counted():
    t = fresh()
    t = LS.observe(t, limits(), now_ms=T0 + 1, equity=D('9900'), unrealized_pnl=D('-100'))
    nxt = ms('2026-11-04T22:00:00+00:00')
    t = LS.observe(t, limits(), now_ms=nxt, equity=D('9900'), unrealized_pnl=D('-100'))
    # Close it at -150: realized -150 today, unrealized back to 0 (+100 vs day start).
    t = LS.record_close(t, strategy_id='trend', realized_pnl=D('-150'), stopped_out=True, now_ms=nxt + 1)
    t = LS.observe(t, limits(), now_ms=nxt + 2, equity=D('9850'), unrealized_pnl=D('0'))
    state = snap(t, nxt + 2)
    assert state.realized_pnl + state.unrealized_pnl == D('-50')         # 9900 -> 9850
