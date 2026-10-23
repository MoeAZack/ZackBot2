"""#183: Binance -2027 (max allowable position at the current leverage) is refused locally before any order. Runtime
defect 22 Oct 20:30 Cairo: the MAXQTY-01 tier gate read the 1 h bracket cache only, so an hour into a run it silently
passed every entry. Now the schedule is re-fetched, an unavailable schedule refuses, and the cap follows the leverage
Binance really runs for the coin."""
import os, sys, time
import pytest
sys.path.insert(0, os.path.dirname(__file__))
import trade_audit as TA                                                  # noqa: E402
from test_maxqty01 import BRK, _tier_engine                               # noqa: E402

SG = dict(close=100.0, atr=2.0, time='2026-01-01T00:00:00')             # 1.0 BTC-unit at 100 = 100 USDT


def _src(e, brk=BRK, fail=None):
    """Give the fake client a bracket endpoint (the real binance_client has one)."""
    calls = []

    def leverage_brackets(sym):
        calls.append(sym)
        if fail: raise fail
        return [dict(x) for x in brk]
    e.trade.leverage_brackets = leverage_brackets
    return calls


def _sleeve():
    import engine as E
    return E.sleeve('A', 'ema_st', 1.0, .01, 2, ['BTCUSDT'])


def _expire(e, sym='BTCUSDT'):
    import engine as E
    t, b = e._brk[sym]
    e._brk[sym] = (time.time() - E.LEV_BRACKET_TTL_S - 1, b)


def test_runtime_repro_an_expired_cache_no_longer_lets_an_over_cap_entry_through():
    e = _tier_engine(20); calls = _src(e)                                 # at 20x Binance allows 50 USDT
    e._lev['BTCUSDT'] = 20                                                # leverage already set earlier in the run
    _expire(e)                                                            # an hour later
    n_open = e.trade.calls.count('open')
    assert e.open_lot(_sleeve(), 'BTCUSDT', 'LONG', SG, None, 500.0) is False
    assert e.last_skip.startswith('leverage tier: BTCUSDT position 100') and e.trade.calls.count('open') == n_open
    assert calls == ['BTCUSDT'] and time.time() - e._brk['BTCUSDT'][0] < 5          # re-fetched and re-cached
    assert not e.state['lots'] and not (e.state.get('pending_entries') or {})


def test_unavailable_schedule_refuses_the_entry_with_nothing_sent():
    e = _tier_engine(20); _src(e, fail=ConnectionError('testnet down'))
    e._brk.clear()
    n_open = e.trade.calls.count('open')
    assert e._tier_block('BTCUSDT', 1.0, 100.0).startswith('leverage tier: BTCUSDT bracket schedule unavailable - entry refused')
    assert e.open_lot(_sleeve(), 'BTCUSDT', 'LONG', SG, None, 500.0) is False
    assert e.trade.calls.count('open') == n_open and not e.state['lots']
    assert not (e.state.get('pending_entries') or {}) and not (e.state.get('unconfirmed_entries') or {})


def test_malformed_schedule_refuses():
    e = _tier_engine(20); _src(e, brk=[dict(floor=0, cap=-1, mmr=0.01, cum=0.0, lev=20)])
    e._brk.clear()
    assert 'entry refused' in e._tier_block('BTCUSDT', 1.0, 100.0)


@pytest.mark.parametrize('notional,ok', [(49.99, True), (50.0, True), (50.01, False)], ids=['below', 'at', 'above'])
def test_boundary_just_below_at_and_above_the_cap(notional, ok):
    e = _tier_engine(20); _src(e)
    assert (e._tier_block('BTCUSDT', notional, 100.0) is None) is ok


@pytest.mark.parametrize('order,ok', [(20.0, True), (25.0, False)])
def test_existing_same_direction_exposure_counts(order, ok):
    e = _tier_engine(20); _src(e)
    e.state['lots']['X'] = dict(symbol='BTCUSDT', side='LONG', qty=0.3, avg=100.0)      # 30 USDT held
    assert (e._tier_block('BTCUSDT', order, 100.0) is None) is ok                     # cap 50 at 20x


def test_the_cap_follows_the_leverage_binance_reports_when_it_is_higher():
    e = _tier_engine(10); _src(e)                                         # configured 10x: 200 USDT allowed
    assert e._tier_block('BTCUSDT', 100.0, 100.0) is None
    e._lev_cur['BTCUSDT'] = 20                                            # the venue still runs 20x: 50 USDT
    assert e._tier_block('BTCUSDT', 100.0, 100.0).endswith('50 USDT at 20x')
    e._lev_cur['BTCUSDT'] = 5                                             # lower than configured: the configured cap stays
    assert e._tier_block('BTCUSDT', 100.0, 100.0) is None


def test_ensure_leverage_records_the_venue_leverage():
    e = _tier_engine(10); _src(e)
    e._lev['BTCUSDT'] = 10
    e._margin_state = lambda s: (7, 'CROSSED')                            # the fast path reads what Binance runs
    assert e._ensure_leverage('BTCUSDT') == 'set' and e._lev_cur['BTCUSDT'] == 7
    e._lev.clear(); e.trade.set_leverage = lambda s, l: None
    assert e._ensure_leverage('BTCUSDT') == 'set' and e._lev_cur['BTCUSDT'] == 10     # a successful set


def test_refusals_keep_the_stable_reason_code():
    e = _tier_engine(20); _src(e, fail=ConnectionError('x')); e._brk.clear()
    for txt in (e._tier_block('BTCUSDT', 1.0, 100.0), 'leverage tier: BTCUSDT position 100 USDT would exceed the Binance maximum 50 USDT at 20x'):
        assert TA.reason_code(txt) == ('execution', 'lev_tier')


def test_venue_leverage_above_every_bracket_refuses():
    e = _tier_engine(20); _src(e)
    e._lev_cur['BTCUSDT'] = 50                                            # above the coin's 20x maximum
    assert e._tier_block('BTCUSDT', 1.0, 100.0) == 'leverage tier: BTCUSDT no bracket allows 50x - entry refused'


def test_an_unexpected_failure_refuses_never_passes_silently():
    e = _tier_engine(20); _src(e)
    e.state['lots']['X'] = dict(symbol='BTCUSDT', side='LONG', qty='junk', avg=100.0)
    assert e._tier_block('BTCUSDT', 1.0, 100.0).startswith('leverage tier: BTCUSDT check failed - entry refused')
