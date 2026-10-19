"""MAXQTY-01: Binance's per-order maximum (MARKET_LOT_SIZE maxQty) is parsed and enforced locally.

2026-10-19 08:00 Cairo (PAPER/testnet): an approved STRKUSDT short was refused by Binance with -4005 'Quantity greater than
max quantity' because rules_from_exchange_info dropped maxQty, so size_check could not refuse it first. An oversized order is
now SKIPPED with a reason before anything is sent - never cut down to the maximum, never transmitted."""
import os, types
import pytest
import feasibility as F
import trade_audit as TA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _info(max_qty='__absent__', sym='STRKUSDT'):
    mls = {'filterType': 'MARKET_LOT_SIZE', 'stepSize': '1', 'minQty': '1'}
    if max_qty != '__absent__': mls['maxQty'] = max_qty
    return {'symbols': [{'symbol': sym, 'contractType': 'PERPETUAL', 'status': 'TRADING', 'filters': [
        mls, {'filterType': 'PRICE_FILTER', 'tickSize': '0.00001'}, {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]}]}


# ------------------------------------------------------------------ parsing
def test_parser_keeps_max_qty():
    assert F.rules_from_exchange_info(_info('1000'))['STRKUSDT']['max_qty'] == 1000.0


@pytest.mark.parametrize('raw', ['__absent__', '0', '', None, '-5', 'abc', 'nan', 'inf'])
def test_parser_without_a_real_maximum_adds_no_key_and_does_not_crash(raw):
    r = F.rules_from_exchange_info(_info(raw))['STRKUSDT']
    assert 'max_qty' not in r and r['step'] == 1.0 and r['min_qty'] == 1.0


def test_exchange_info_round_trip_and_snapshot_diff_keep_max_qty():
    rules = F.rules_from_exchange_info(_info('1000'))
    assert F.rules_from_exchange_info(F.exchange_info_from_rules(rules)) == rules
    legacy = {'X': dict(step=1.0, min_qty=1.0, tick=0.1, min_notional=5.0)}
    assert F.rules_from_exchange_info(F.exchange_info_from_rules(legacy)) == legacy           # no max -> still no key
    d = F.diff_snapshots({'symbols': rules}, {'symbols': {'STRKUSDT': dict(rules['STRKUSDT'], max_qty=500.0)}})
    assert d['changed'] == {'STRKUSDT': {'max_qty': (1000.0, 500.0)}}


# ------------------------------------------------------------------ the shared check
R = dict(step=1.0, min_qty=1.0, max_qty=1000.0, tick=0.00001, min_notional=5.0)


@pytest.mark.parametrize('q', [1001, 1000.9 + 1, 5000, 1e6])
def test_oversized_order_is_refused_never_cut_down(q):
    d = F.size_check(q, q, 0.2, R)
    assert d['ok'] is False and d['code'] == 'above_max_qty' and d['reason'] == F.REASON_ABOVE_MAX
    assert d['qty'] > R['max_qty']                                       # reported as asked, never silently capped


def test_exact_max_and_step_floored_below_max_pass():
    assert F.size_check(1000, 1000, 0.2, R) == dict(ok=True, qty=1000.0, code='ok', reason='')
    assert F.size_check(1000.7, 1000.7, 0.2, R)['qty'] == 1000.0 and F.size_check(1000.7, 1000.7, 0.2, R)['ok'] is True


def test_off_grid_max_is_never_exceeded():
    r = dict(R, max_qty=999.5)
    assert F.size_check(999.9, 999.9, 0.2, r)['ok'] is True and F.size_check(999.9, 999.9, 0.2, r)['qty'] == 999.0
    assert F.size_check(1000, 1000, 0.2, r)['code'] == 'above_max_qty'


def test_leverage_capped_quantity_is_what_is_checked():
    assert F.size_check(3000, 900, 0.2, R)['ok'] is True                 # the cap already brought it under the maximum
    assert F.size_check(3000, 1200, 0.2, R)['code'] == 'above_max_qty'   # still over: refused, not sent


def test_rules_without_max_are_unchanged():
    legacy = dict(step=1.0, min_qty=1.0, tick=0.00001, min_notional=5.0)
    assert F.size_check(1e9, 1e9, 0.2, legacy)['ok'] is True
    assert F.size_check(1e9, 1e9, 0.2, dict(legacy, max_qty=0))['ok'] is True
    assert F.required_qty(0.2, R) == F.required_qty(0.2, legacy)


# ------------------------------------------------------------------ engine entry / add / reason text
def test_engine_entry_above_max_is_skipped_without_an_order():
    import test_safety as TS
    import engine as E
    e, _ = TS.mk_engine()
    sl = E.sleeve('A', 'ema_st', 1.0, .01, 2, ['BTCUSDT'])
    sg = dict(close=100.0, atr=2.0, time='2026-01-01T00:00:00')
    # risk 500 * 1% = 5 USDT / (2.5 * 2) = 1.0 BTC-unit; Binance allows at most 0.5 per order
    e.rules['BTCUSDT'] = dict(step=0.001, min_qty=0.001, max_qty=0.5, tick=0.01, min_notional=5.0)
    n_open = e.trade.calls.count('open')
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', sg, None, 500.0) is False
    assert e.last_skip == F.REASON_ABOVE_MAX and e.trade.calls.count('open') == n_open and not e.state['lots']
    e.rules['BTCUSDT']['max_qty'] = 1.0                                  # exactly the maximum: placed
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', sg, None, 500.0) is True
    assert list(e.state['lots'].values())[0]['qty'] == 1.0


def test_engine_add_above_max_is_refused_without_an_order():
    import test_safety as TS
    import engine as E
    e, _ = TS.mk_engine()
    e.rules['ETHUSDT'] = dict(step=0.001, min_qty=0.001, tick=0.01, min_notional=5.0)
    assert e.open_lot(E.sleeve('B', 'ema_st', 1.0, .01, 2, ['ETHUSDT']), 'ETHUSDT', 'LONG',
                      dict(close=50.0, atr=1.0, time='2026-01-01T00:00:00'), None, 500.0) is True
    lot = list(e.state['lots'].values())[0]; q0 = lot['qty']; n_open = e.trade.calls.count('open')
    e.rules['ETHUSDT']['max_qty'] = 0.5
    assert e._add_qty(lot, 0.6, 50.0, 'pyramid_add') is False
    assert e.trade.calls.count('open') == n_open and lot['qty'] == q0
    assert e._add_qty(lot, 0.5, 50.0, 'pyramid_add') is True
    assert lot['qty'] == pytest.approx(q0 + 0.5)


def test_engine_skip_text_is_the_shared_reason_and_maps_to_its_audit_code():
    src = open(os.path.join(ROOT, 'engine.py'), encoding='utf-8').read()
    assert f"'{F.REASON_ABOVE_MAX}'" in src
    assert TA.reason_code(F.REASON_ABOVE_MAX) == ('execution', 'size_max')


# ------------------------------------------------------------------ grid preflight
def test_grid_order_above_max_is_not_sent():
    import grid as G
    e = types.SimpleNamespace(persist_block=lambda: None,
                              rules={'STRKUSDT': dict(step=1.0, min_qty=1.0, max_qty=1000.0, tick=0.00001, min_notional=5.0)})
    g = dict(sym='STRKUSDT', stop_lo=0.1, stop_hi=0.3, capital=1e6, cfg={}, hi=0.25, lo=0.15)
    gm = G.GridManager(e)
    gm._order_ok(g, 1000, 0.2)                                           # at the maximum: fine
    with pytest.raises(ValueError, match='maximum order quantity'):
        gm._order_ok(g, 1001, 0.2)


# ------------------------------------------------------------------ leverage tier (Binance -2027)
BRK = [dict(floor=0, cap=50.0, mmr=0.01, cum=0.0, lev=20), dict(floor=50.0, cap=200.0, mmr=0.02, cum=0.5, lev=10),
       dict(floor=200.0, cap=1e6, mmr=0.05, cum=6.5, lev=2)]


def test_tier_cap_is_the_largest_cap_allowed_at_the_leverage():
    assert F.tier_notional_cap(BRK, 20) == 50.0 and F.tier_notional_cap(BRK, 10) == 200.0
    assert F.tier_notional_cap(BRK, 3) == 200.0 and F.tier_notional_cap(BRK, 2) == 1e6 and F.tier_notional_cap(BRK, 25) is None
    assert F.tier_notional_cap(None, 10) is None and F.tier_notional_cap([dict(cap='x', lev=10)], 10) is None


def _tier_engine(max_lev):
    import time
    import test_safety as TS
    e, _ = TS.mk_engine()
    e.S['MAX_LEVERAGE'] = max_lev
    e.rules['BTCUSDT'] = dict(step=0.001, min_qty=0.001, tick=0.01, min_notional=5.0)
    e._brk['BTCUSDT'] = (time.time(), [dict(x) for x in BRK])
    return e


def test_engine_entry_over_the_leverage_tier_is_skipped_without_an_order():
    import engine as E
    sl = E.sleeve('A', 'ema_st', 1.0, .01, 2, ['BTCUSDT'])
    sg = dict(close=100.0, atr=2.0, time='2026-01-01T00:00:00')         # 1.0 BTC-unit at 100 = 100 USDT
    e = _tier_engine(20)                                                 # at 20x Binance allows 50 USDT
    n_open = e.trade.calls.count('open')
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', sg, None, 500.0) is False
    assert e.last_skip.startswith('leverage tier: BTCUSDT') and e.trade.calls.count('open') == n_open
    assert TA.reason_code(e.last_skip) == ('execution', 'lev_tier')
    e = _tier_engine(10)                                                 # at 10x: 200 USDT allowed
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', sg, None, 500.0) is True
    lot = list(e.state['lots'].values())[0]; q0 = lot['qty']; n_open = e.trade.calls.count('open')
    assert e._add_qty(lot, 1.5, 100.0, 'pyramid_add') is False          # 100 held + 150 > 200: refused, nothing sent
    assert e.trade.calls.count('open') == n_open and lot['qty'] == q0
    assert e._add_qty(lot, 0.5, 100.0, 'pyramid_add') is True           # 150 <= 200


def test_unknown_bracket_schedule_never_blocks():
    e = _tier_engine(20); e._brk.clear()
    assert e._tier_block('BTCUSDT', 1e9) is None


def test_grid_order_over_the_leverage_tier_is_not_sent():
    import grid as G
    e = types.SimpleNamespace(persist_block=lambda: None, rules={'STRKUSDT': dict(step=1.0, min_qty=1.0, tick=0.00001, min_notional=5.0)},
                              _tier_block=lambda s, n: 'leverage tier: STRKUSDT over' if n > 100 else None)
    g = dict(sym='STRKUSDT', stop_lo=0.1, stop_hi=0.3, capital=1e6, cfg={}, hi=0.25, lo=0.15)
    G.GridManager(e)._order_ok(g, 500, 0.2)                              # 100 USDT: fine
    with pytest.raises(ValueError, match='leverage tier'):
        G.GridManager(e)._order_ok(g, 501, 0.2)
