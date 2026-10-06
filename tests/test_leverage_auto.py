"""T03c: automatic leverage handling. When Binance refuses to lower a coin's leverage and the coin stays above the cap, an
entry may still go ahead - but only under CROSS margin, and only when a FRESH exchange snapshot proves the whole account
(every position, every working order, every confirmed stop) stays within the cap and far from liquidation if every stop
fills. Every unknown answer keeps the T03a skip. Round 1 (Codex review) groups 1-7 are marked below."""
import json, math, os, shutil, subprocess, sys, tempfile, time
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import mk_engine, SL, SG, opened, client_with, Resp     # noqa: E402
import engine as E                                                      # noqa: E402
import binance_client as BC                                             # noqa: E402
import grid as GRID                                                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# A Binance-shaped schedule (BTCUSDT-like): contiguous tiers, cum continuous (50 = 50000 * (0.005 - 0.004), ...)
BRK = [dict(floor=0.0, cap=50000.0, mmr=0.004, cum=0.0, lev=125.0), dict(floor=50000.0, cap=250000.0, mmr=0.005, cum=50.0, lev=100.0),
       dict(floor=250000.0, cap=3e6, mmr=0.01, cum=1300.0, lev=50.0), dict(floor=3e6, cap=1e9, mmr=0.025, cum=46300.0, lev=20.0)]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(E.time, 'sleep', lambda s: None)


def refusing(cur=20, mtype='CROSSED', bal=5000.0, mm=0.0, **S):
    """Binance refuses every leverage change; the coin stays at `cur`x with margin type `mtype`. The fake exchange answers
    the fresh-snapshot reads from its own state: positions (t.pos + t.extra_pos), open orders (its stops + t.extra_orders),
    the account (t.acc) and the bracket schedule (t.brk)."""
    e, _ = mk_engine(MAX_LEVERAGE=10, **S)
    t = e.trade
    t.fail.add('leverage')
    t.extra_pos, t.extra_orders, t.brk = {}, [], {'BTCUSDT': BRK, 'ETHUSDT': BRK}
    t.acc = {'totalMarginBalance': str(bal), 'totalMaintMargin': str(mm), 'totalUnrealizedProfit': '0'}
    t.margin_state = lambda s: (t.calls.append('mstate'), dict(leverage=cur, margin_type=mtype))[1]
    t.account = lambda: (t.calls.append('account'), dict(t.acc))[1]

    def position_risk():
        t.calls.append('positions_all'); rows = []
        for (s, ps), q in list(t.pos.items()) + list(t.extra_pos.items()):
            if q > 1e-12: rows.append(dict(symbol=s, side=ps, qty=q, mark=t.mark[s], notional=q * t.mark[s]))
        return rows

    def open_orders_all():
        t.calls.append('orders_all')
        return [dict(tag=k, symbol=s, side='SELL' if ps == 'LONG' else 'BUY', position_side=ps, qty=q, price=0.0, stop_price=p,
                     reduce_only=False, client_id=None) for k, (s, ps, q, p) in t.stops.items()] + list(t.extra_orders)

    def leverage_brackets(s):
        t.calls.append('brackets'); b = t.brk[s]
        if isinstance(b, Exception): raise b
        return [dict(x) for x in b]
    t.position_risk, t.open_orders_all, t.leverage_brackets = position_risk, open_orders_all, leverage_brackets
    return e


def lev_posts(e): return sum(1 for c in e.trade.calls if c == 'leverage')


def add_lot(e, sym='ETHUSDT', side='LONG', qty=10.0, avg=50.0, stop=45.0, on_exchange=None, with_stop=True, **kw):
    """An open bot lot WITH its exchange position and confirmed exchange stop (unless told otherwise)."""
    t = e.trade; key = f'Z|{sym}|{side}|{len(e.state["lots"])}'
    lot = dict(symbol=sym, side=side, qty=qty, q0=qty, avg=avg, e0=avg, stop=stop, sleeve='Z', stop_id=None, stop_dirty=False,
               mgmt={}, adds=0, dca=0, R=abs(avg - stop) if isinstance(stop, float) else 1.0, manual=False)
    lot.update(kw)
    t.pos[(sym, side)] = t.pos.get((sym, side), 0.0) + (qty if on_exchange is None else on_exchange)
    if with_stop:
        t.n += 1; tag = f'o:{t.n}'; t.stops[tag] = (sym, side, qty, stop); lot['stop_id'] = tag
    e.state['lots'][key] = lot
    return key, lot


def check(e, notional=100.0, risk=5.0, sym='BTCUSDT'):
    return e._exposure_check(sym, notional, risk)


def entry_skipped(e, *words, eq=None):
    n_open = e.trade.calls.count('open')
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, eq or e.equity())
    assert e.trade.calls.count('open') == n_open and not any(l['symbol'] == 'BTCUSDT' for l in e.state['lots'].values())
    for w in words: assert w in e.last_skip, e.last_skip
    return e.lev_refusals['BTCUSDT']


# ------------------------------------------------------------------ the T03c behaviour (original tests, on a fresh snapshot)
def test_cross_margin_above_cap_enters_when_the_exposure_is_safe():
    e = refusing()
    k = opened(e)
    lot = e.state['lots'][k]
    assert lot['stop_id'] in e.trade.stops and lot['lev_exception'] is True        # entry with its exchange stop, flagged
    r = e.lev_refusals['BTCUSDT']
    assert (r['count'], r['api_refusals'], r['proceeded'], r['by_exposure'], r['skipped'], r['accepted']) == (1, 1, 1, 1, 0, 'exposure')
    assert r['current'] == 20 and r['margin_type'] == 'CROSSED' and r['exposure']['ok'] is True and r['exposure']['check'] is None
    assert r['exposure']['effective_leverage'] <= 10 and r['exposure']['worst_margin_ratio'] <= 0.5
    assert (r['outcome'], r['reason'], r['via']) == ('went_ahead', 'exposure_ok', 'refused')
    assert 'BTCUSDT' not in e._lev, 'never cached: the exchange is still above the cap'


def test_the_entry_size_reaches_the_check():
    e = refusing(); seen = {}
    real = e._exposure_check
    e._exposure_check = lambda sym, n, r: (seen.update(n=n, r=r), real(sym, n, r))[1]
    k = opened(e); lot = e.state['lots'][k]
    assert abs(seen['n'] - lot['qty'] * 100.0) < 1e-6 and seen['r'] > 0


@pytest.mark.parametrize('mtype', ['ISOLATED', None])
def test_isolated_or_unknown_margin_type_skips(mtype):
    e = refusing(mtype=mtype)
    r = entry_skipped(e, 'not cross')
    assert r['skipped'] == 1 and r['by_exposure'] == 0
    assert r['reason'] == ('margin_isolated' if mtype else 'margin_unknown') and r['outcome'] == 'skipped'


def test_missing_account_margin_data_skips():
    e = refusing()
    e.trade.acc = {'totalMarginBalance': '5000'}                                  # no totalMaintMargin
    r = entry_skipped(e, 'account margin data unavailable')
    assert r['exposure']['check'] == 'account'


def test_account_effective_leverage_above_the_cap_skips():
    e = refusing()
    add_lot(e, qty=1000.0, avg=50.0, stop=49.99)                                  # 50 000 USDT open, protected
    r = entry_skipped(e, 'effective leverage')
    assert r['exposure']['ok'] is False and r['exposure']['check'] == 'effective_leverage'


def test_worst_case_margin_ratio_above_the_limit_skips():
    e = refusing(mm=2600.0)                                                      # maintenance margin already > 50% of 5000
    r = entry_skipped(e, 'worst-case margin ratio')
    assert r['exposure']['check'] == 'worst_margin_ratio'


def test_an_exception_in_the_check_skips():
    e = refusing()
    def boom(*a): raise RuntimeError('account endpoint down')
    e._exposure_proof = boom                                                     # anything unexpected inside the proof
    r = entry_skipped(e, 'exposure check failed')
    assert r['exposure']['check'] == 'error'


def test_unknown_current_leverage_still_skips():
    e = refusing(cur=None)
    r = entry_skipped(e, 'current leverage unknown')
    assert r['reason'] == 'leverage_unknown' and r['exposure'] is None


def test_within_cap_path_is_unchanged_and_needs_no_exposure_check():
    e = refusing(cur=5, mtype='ISOLATED')
    e._exposure_check = lambda *a: pytest.fail('not needed when the coin is already within the cap')
    k = opened(e)
    r = e.lev_refusals['BTCUSDT']
    assert r['accepted'] == 'within_cap' and r['by_exposure'] == 0 and r['reason'] == 'within_cap'
    assert 'lev_exception' not in e.state['lots'][k], 'within the cap: adds stay allowed'


def test_a_refusal_starts_a_cooldown_without_new_change_requests(monkeypatch):
    e = refusing()
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    opened(e)
    assert lev_posts(e) == 2                                                     # first try + one retry
    e.close_lot(next(iter(e.state['lots'])), 'signal', mark=100.0)
    t[0] += 60; opened(e)
    assert lev_posts(e) == 2, 'inside the cooldown: no new leverage change request'
    r = e.lev_refusals['BTCUSDT']
    assert (r['count'], r['api_refusals'], r['cooldown_checks'], r['via']) == (2, 1, 1, 'cooldown')
    assert 'injected' in r['last_api_error'] and 'injected' in r['last_error'], 'the real Binance error is preserved'
    e.close_lot(next(iter(e.state['lots'])), 'signal', mark=100.0)
    t[0] += E.LEV_REFUSAL_COOLDOWN_S; opened(e)
    assert lev_posts(e) == 4, 'after the cooldown the change is requested again'
    assert e.lev_refusals['BTCUSDT']['api_refusals'] == 2


def test_cooldown_still_applies_every_safety_check():
    e = refusing()
    opened(e); e.close_lot(next(iter(e.state['lots'])), 'signal', mark=100.0)
    e.trade.margin_state = lambda s: dict(leverage=20, margin_type='ISOLATED')
    r = entry_skipped(e, 'not cross')
    assert r['via'] == 'cooldown' and r['reason'] == 'margin_isolated'


def test_a_later_successful_change_clears_the_cooldown_and_caches(monkeypatch):
    e = refusing()
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    opened(e); e.close_lot(next(iter(e.state['lots'])), 'signal', mark=100.0)
    e.trade.fail.discard('leverage'); t[0] += E.LEV_REFUSAL_COOLDOWN_S + 1
    opened(e)
    assert e._lev['BTCUSDT'] == 10 and 'BTCUSDT' not in e._lev_cool


def test_client_reads_margin_state_from_position_risk():
    rows = [dict(symbol='SOLUSDT', leverage='20', marginType='cross', positionSide='LONG'),
            dict(symbol='SOLUSDT', leverage='20', marginType='cross', positionSide='SHORT')]
    c, calls = client_with([Resp(200, rows)])
    assert c.margin_state('SOLUSDT') == dict(leverage=20, margin_type='CROSSED')
    c, _ = client_with([Resp(200, [dict(rows[0], marginType='isolated')])])
    assert c.margin_state('SOLUSDT')['margin_type'] == 'ISOLATED'
    c, _ = client_with([Resp(200, [rows[0], dict(rows[1], marginType='isolated')])])
    assert c.margin_state('SOLUSDT')['margin_type'] is None                     # mixed answer = unknown
    c, _ = client_with([Resp(200, [])])
    assert c.margin_state('SOLUSDT') == dict(leverage=None, margin_type=None)
    c, _ = client_with([Resp(200, [dict(rows[0], symbol='BTCUSDT')])])
    assert c.margin_state('SOLUSDT') == dict(leverage=None, margin_type=None)   # rows for another coin are ignored


def test_dry_mode_never_reaches_the_leverage_path():
    e = refusing(); e.dry = True
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'leverage' not in e.trade.calls and e.lev_refusals == {}


# ------------------------------------------------------------------ group 1: account-wide exposure, reconcile, working entries
def test_g1_unrelated_untracked_position_rejects():
    e = refusing()
    e.trade.extra_pos[('ETHUSDT', 'LONG')] = 1000.0                              # 50 000 USDT manual ETH position, no lot
    ok, why, n = check(e)
    assert not ok and n['check'] == 'reconcile' and 'ETHUSDT LONG' in why
    entry_skipped(e, 'Binance holds 1000')


def test_g1_engine_untracked_flag_rejects():
    e = refusing(); e.untracked = {'ETHUSDT|SHORT': 3.0}
    ok, why, n = check(e)
    assert not ok and n['check'] == 'reconcile' and 'untracked' in why


@pytest.mark.parametrize('on_exchange', [30.0, 0.0, 9.0])
def test_g1_filled_but_unreconciled_lot_rejects(on_exchange):
    e = refusing()
    add_lot(e, qty=10.0, on_exchange=on_exchange)                                # exchange holds more / nothing / less
    ok, why, n = check(e)
    assert not ok and n['check'] == 'reconcile', why


def test_g1_reconciled_lot_within_step_tolerance_passes():
    e = refusing()
    add_lot(e, qty=10.0, on_exchange=10.0005)                                    # inside the 0.001 step tolerance
    ok, why, n = check(e)
    assert ok, why


def test_g1_pending_order_or_unconfirmed_grid_rejects():
    e = refusing()
    k, lot = add_lot(e); lot['pending'] = dict(kind='add', qty=1.0)
    assert check(e)[2]['check'] == 'reconcile'
    del lot['pending']; e.state['grids'] = {'G|ETHUSDT': dict(sym='ETHUSDT', op=dict(kind='add'), metrics={})}
    assert check(e)[2]['check'] == 'reconcile'


def resting(e, sym='ETHUSDT', side='LONG', qty=1.0, price=50.0, stop_dist=2.0, cid='zmX', filled=0.0):
    k = f'ME|Z|{sym}|{side}|{len(e.state["resting_entries"])}'
    e.state['resting_entries'][k] = dict(key=k, sleeve='Z', symbol=sym, side=side, qty=qty, filled=filled, cost=0.0, n=1,
                                         t0=time.time(), status='open', cid=cid, price=price, plan=dict(px=price, stop_dist=stop_dist))
    e.trade.extra_orders.append(dict(tag=f'o:9{len(e.trade.extra_orders)}', symbol=sym, side='BUY' if side == 'LONG' else 'SELL',
                                     position_side=side, qty=qty - filled, price=price, stop_price=0.0, reduce_only=False, client_id=cid))
    return k


def test_g1_one_large_resting_entry_is_reserved_and_rejects():
    e = refusing()
    resting(e, qty=1000.0)                                                       # 50 000 USDT maker entry, not a lot yet
    ok, why, n = check(e)
    assert not ok and n['check'] == 'effective_leverage' and n['reserved_notional'] >= 50000


def test_g1_several_resting_entries_are_all_reserved():
    e = refusing()
    resting(e, qty=1.0, cid='a'); resting(e, sym='BTCUSDT', qty=2.0, price=100.0, cid='b')
    ok, why, n = check(e)
    assert ok, why
    assert n['reserved_notional'] == pytest.approx(1.0 * 50 + 2.0 * 100)
    assert n['gross_notional'] == pytest.approx(50 + 200 + 100)                  # reserved + this entry


def test_g1_partially_filled_resting_entry_on_the_exchange_reconciles():
    e = refusing()
    resting(e, qty=1.0, filled=0.5); e.trade.extra_pos[('ETHUSDT', 'LONG')] = 0.5   # fill seen on Binance before it is a lot
    ok, why, n = check(e)
    assert ok, why


def test_g1_unknown_working_order_rejects_but_reduce_only_and_bot_entries_do_not():
    e = refusing()
    e.trade.extra_orders.append(dict(tag='o:777', symbol='ETHUSDT', side='SELL', position_side='LONG', qty=5.0, price=60.0,
                                     stop_price=0.0, reduce_only=False, client_id='manual-tp'))       # closes: fine
    e.trade.extra_orders.append(dict(tag='o:778', symbol='ETHUSDT', side='BUY', position_side='BOTH', qty=5.0, price=40.0,
                                     stop_price=0.0, reduce_only=True, client_id=None))               # reduce-only: fine
    assert check(e)[0]
    resting(e, qty=1.0, cid='zmBOT')
    assert check(e)[0]
    e.trade.extra_orders.append(dict(tag='a:55', symbol='ETHUSDT', side='SELL', position_side='SHORT', qty=100.0, price=0.0,
                                     stop_price=45.0, reduce_only=False, client_id='manual'))         # manual conditional entry
    ok, why, n = check(e)
    assert not ok and n['check'] == 'working_orders'


def test_g1_active_grid_and_future_adds_are_reserved():
    e = refusing()
    e.state['grids'] = {'G|ETHUSDT': dict(sym='ETHUSDT', op=None, mode='neutral', metrics=dict(max_notional=300.0, worst_loss_usd=20.0))}
    k, lot = add_lot(e, qty=1.0, avg=50.0, stop=40.0, levels=[45.0, 42.0], w=[2.0, 4.0], q0=1.0)    # 2 DCA orders left
    k2, lot2 = add_lot(e, sym='BTCUSDT', qty=0.1, avg=100.0, stop=90.0, adds=1, next_add=110.0,
                       mgmt=dict(pyramid=dict(n=3, frac=0.5, step_r=1.0)), q0=0.1)              # 2 pyramid adds left
    ok, why, n = check(e)
    assert ok, why
    dca = 2.0 * 50 + 4.0 * 50                     # each level valued at max(level, mark=50)
    pyr = 0.05 * 110 + 0.05 * 120                 # next_add 110, then +1R (R=10)
    assert n['reserved_notional'] == pytest.approx(2 * 300.0 + dca + pyr)


# ------------------------------------------------------------------ group 2: confirmed stops on every lot (any symbol)
def test_g2_stop_dirty_lot_on_another_symbol_rejects():
    e = refusing(); k, lot = add_lot(e); lot['stop_dirty'] = True
    ok, why, n = check(e); assert not ok and n['check'] == 'stops' and 'not confirmed' in why
    entry_skipped(e, 'stop not confirmed')


def test_g2_missing_stop_id_rejects():
    e = refusing(); add_lot(e, with_stop=False)
    ok, why, n = check(e); assert not ok and n['check'] == 'stops'


def test_g2_stale_stop_not_open_on_binance_rejects():
    e = refusing(); k, lot = add_lot(e)
    e.trade.stops.pop(lot['stop_id'])                                            # the engine still believes it is there
    ok, why, n = check(e); assert not ok and n['check'] == 'stops' and 'not open on Binance' in why


def test_g2_stop_on_the_wrong_symbol_side_or_too_small_rejects():
    e = refusing(); k, lot = add_lot(e)
    s, ps, q, p = e.trade.stops[lot['stop_id']]
    for bad in [('BTCUSDT', ps, q, p), (s, 'SHORT', q, p), (s, ps, q / 2, p)]:
        e.trade.stops[lot['stop_id']] = bad
        ok, why, n = check(e); assert not ok and n['check'] == 'stops', bad
    e.trade.stops[lot['stop_id']] = (s, ps, q, p)
    assert check(e)[0]


def test_g2_lot_without_a_numeric_stop_rejects():
    e = refusing()
    for bad in (None, 0, 'x', float('nan'), float('inf'), True):
        e.state['lots'] = {}; e.trade.pos.clear(); e.trade.stops.clear()
        add_lot(e, stop=bad)
        ok, why, n = check(e)
        assert not ok and why == 'an open lot has no known stop' and n['check'] == 'stops', bad


def test_g2_snapshot_read_failures_reject():
    e = refusing()
    def down(): raise BC.BinanceError(-1000, 'algo orders unavailable')
    e.trade.open_orders_all = down
    ok, why, n = check(e); assert not ok and n['check'] == 'snapshot'


def test_g2_client_open_orders_all_raises_when_the_algo_read_fails():
    orders = [dict(orderId=1, symbol='ETHUSDT', side='SELL', positionSide='LONG', origQty='2', executedQty='0', price='0',
                   stopPrice='45', reduceOnly=False, closePosition=False, clientOrderId='x')]
    c, calls = client_with([Resp(200, orders), Resp(200, {'orders': [dict(algoId=9, symbol='ETHUSDT', side='BUY', positionSide='SHORT',
                                                                            quantity='3', triggerPrice='55', clientAlgoId='y')]})])
    out = c.open_orders_all()
    assert [o['tag'] for o in out] == ['o:1', 'a:9'] and out[0]['qty'] == 2.0 and out[1]['stop_price'] == 55.0
    assert all('symbol' not in p for _, _, p in calls), 'account-wide reads'
    c, _ = client_with([Resp(200, orders), Resp(400, {'code': -1000, 'msg': 'no algo'})])
    with pytest.raises(BC.BinanceError): c.open_orders_all()


def test_g2_client_position_risk_reads_every_symbol_and_side():
    rows = [dict(symbol='ETHUSDT', positionAmt='2', positionSide='LONG', markPrice='50', notional='100'),
            dict(symbol='ETHUSDT', positionAmt='-1', positionSide='SHORT', markPrice='50', notional='-50'),
            dict(symbol='BTCUSDT', positionAmt='0', positionSide='LONG', markPrice='100', notional='0')]
    c, calls = client_with([Resp(200, rows)])
    assert c.position_risk() == [dict(symbol='ETHUSDT', side='LONG', qty=2.0, mark=50.0, notional=100.0),
                                 dict(symbol='ETHUSDT', side='SHORT', qty=1.0, mark=50.0, notional=50.0)]
    assert calls[0][1] == '/v2/positionRisk' and 'symbol' not in calls[0][2]


# ------------------------------------------------------------------ group 3: current mark to stop, current notional
@pytest.mark.parametrize('side,avg,stop', [('LONG', 40.0, 45.0),     # profitable long, trailing stop above entry
                                           ('LONG', 60.0, 45.0),     # losing long
                                           ('SHORT', 60.0, 55.0),    # profitable short, trailing stop below entry
                                           ('SHORT', 45.0, 55.0)])   # losing short
def test_g3_loss_is_projected_from_the_current_mark_to_the_stop(side, avg, stop):
    e = refusing(bal=5000.0)
    add_lot(e, side=side, qty=100.0, avg=avg, stop=stop)                         # mark 50, 5 away from every stop
    ok, why, n = check(e, notional=100.0, risk=5.0)
    assert ok, why
    assert n['loss_to_stops'] == pytest.approx(100 * 5 * 1.5 + 5 * 1.5)          # never avg-to-stop
    assert n['balance_after_stops'] == pytest.approx(5000 - 750 - 7.5)
    assert n['gross_notional'] == pytest.approx(100 * 50 + 100)                  # current mark notional, not qty * avg


def test_g3_breakeven_stop_through_the_mark_counts_no_gain():
    e = refusing(); add_lot(e, qty=10.0, avg=40.0, stop=50.0)                    # stop at the mark: zero loss, never a credit
    ok, why, n = check(e); assert ok and n['loss_to_stops'] == pytest.approx(7.5)


def test_g3_big_unrealized_gain_given_back_to_a_breakeven_stop_rejects():
    """totalMarginBalance holds the gain; a stop near entry gives it back. Entry-to-stop risk is ~0 here, so the old
    basis accepted this entry; mark-to-stop rejects it."""
    e = refusing(bal=5000.0, mm=200.0)
    k, lot = add_lot(e, qty=400.0, avg=30.0, stop=31.0)                          # mark 50: 8000 unrealized, 7600 given back
    assert e._lot_risk(lot) == 0.0
    ok, why, n = check(e)
    assert not ok and n['check'] == 'worst_margin_ratio' and n['balance_after_stops'] < 0
    assert n['open_risk'] == 0.0, 'entry-to-stop risk is still reported, separately'


def test_g3_short_notional_grows_to_its_stop_for_maintenance():
    e = refusing(bal=100000.0)
    add_lot(e, side='SHORT', qty=100.0, avg=50.0, stop=60.0)
    ok, why, n = check(e, notional=100.0, risk=5.0, sym='ETHUSDT')
    worst = 100 * 50 + 100 * 10 * 1.5 + 100 + 5 * 1.5                            # notional at the slipped stop + entry
    assert n['maint_after_stops'] == pytest.approx(E.bracket_maint(BRK, worst) - E.bracket_maint(BRK, 5000.0), abs=0.01)


# ------------------------------------------------------------------ group 4: adds and maker remainder while above the cap
def exception_lot(e):
    k = opened(e); lot = e.state['lots'][k]
    assert lot['lev_exception'] and e.lev_refusals['BTCUSDT']['accepted'] == 'exposure'
    return k, lot


def test_g4_dca_and_pyramid_adds_are_blocked_while_above_the_cap(monkeypatch):
    e = refusing()
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    k, lot = exception_lot(e)
    e.trade.acc['totalMarginBalance'] = '1000000'                                # the account got much safer: still blocked
    n_open = e.trade.calls.count('open')
    for why in ('safety_order', 'pyramid_add'):
        assert e._add_gate(lot, 0.01, 100.0, why), why
    assert 'above your 10x cap' in lot['add_blocked'] and e.trade.calls.count('open') == n_open
    assert any(m.get('kind') == 'add_blocked' for m in e.missed)


def test_g4_pyramid_add_in_manage_sends_no_order(monkeypatch):
    e = refusing()
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    k, lot = exception_lot(e)
    lot['mgmt'] = dict(pyramid=dict(n=2, frac=0.5, step_r=1.0)); lot['next_add'] = 101.0
    e.trade.mark['BTCUSDT'] = 102.0
    n_open = e.trade.calls.count('open')
    e.manage(e.trade.marks())
    assert e.trade.calls.count('open') == n_open and lot['adds'] == 0 and 'above your' in lot['add_blocked']


def test_g4_adds_resume_once_leverage_is_back_within_the_cap(monkeypatch):
    e = refusing()
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    k, lot = exception_lot(e)
    assert e._lev_exception_block('BTCUSDT')
    n0 = e.trade.calls.count('mstate')
    assert e._lev_exception_block('BTCUSDT') and e.trade.calls.count('mstate') == n0, 're-read at most once a minute'
    e.trade.margin_state = lambda s: dict(leverage=10, margin_type='CROSSED')   # changed by hand on Binance
    t[0] += E.LEV_EXC_RECHECK_S
    assert e._lev_exception_block('BTCUSDT') is None and 'lev_exception' not in lot
    k2, lot2 = add_lot(e, sym='ETHUSDT', lev_exception=True)
    e._lev['ETHUSDT'] = 10                                                       # or: the bot set it later
    assert e._lev_exception_block('ETHUSDT') is None and 'lev_exception' not in lot2


def test_g4_grid_adds_are_blocked_on_an_exception_coin(monkeypatch):
    e = refusing()
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    exception_lot(e)
    assert not e.grids._can_add(dict(sym='BTCUSDT'), 'LONG') and e.grids._can_add(dict(sym='ETHUSDT'), 'LONG')


def maker_plan(e):
    seen = {}
    e.S['ENTRY_ORDER'] = 'maker'; e.S['SLEEVES'] = [SL]
    e._maker_start = lambda plan: (seen.update(plan=plan), True)[1]
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    plan = seen['plan']; assert plan['lev_exception'] is True
    k = f"ME|T|BTCUSDT|LONG"
    e.state['resting_entries'][k] = dict(key=k, sleeve='T', symbol='BTCUSDT', side='LONG', qty=plan['qty'], filled=0.0, cost=0.0, n=4,
                                         t0=time.time(), status='between', cid=None, plan=plan, price=100.0)
    return k, plan


def test_g4_maker_remainder_is_not_sent_at_market_after_a_partial_fill(monkeypatch):
    e = refusing()
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    k, plan = maker_plan(e)
    rec = e.state['resting_entries'][k]; rec['filled'] = plan['qty'] / 2; rec['cost'] = rec['filled'] * 100.0
    e.trade.pos[('BTCUSDT', 'LONG')] = rec['filled']
    emitted = []; monkeypatch.setattr(e, '_fill_emit', lambda r: emitted.append(r))
    n_open = e.trade.calls.count('open')
    e._maker_finalize(k, plan['qty'] / 2, False, 100.0)
    assert e.trade.calls.count('open') == n_open, 'no market remainder'
    assert 'above your 10x cap' in emitted[0]['fallback_blocked']
    lot = next(l for l in e.state['lots'].values() if l['symbol'] == 'BTCUSDT')
    assert lot['lev_exception'] and lot['qty'] == pytest.approx(plan['qty'] / 2)


def test_g4_unfilled_maker_entry_gets_no_market_fallback(monkeypatch):
    e = refusing()
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    k, plan = maker_plan(e)
    emitted = []; monkeypatch.setattr(e, '_fill_emit', lambda r: emitted.append(r))
    n_open = e.trade.calls.count('open')
    e._maker_finalize(k, plan['qty'], False, 100.0)
    assert e.trade.calls.count('open') == n_open and not e.state['lots']
    assert 'above your 10x cap' in emitted[0]['fallback_blocked']
    assert 'market fallback blocked' in e.missed[-1]['reason']


def test_g4_maker_fallback_runs_normally_once_leverage_was_set(monkeypatch):
    e = refusing()
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    k, plan = maker_plan(e)
    e._lev['BTCUSDT'] = 10
    monkeypatch.setattr(e, '_fill_emit', lambda r: None)
    e._maker_finalize(k, plan['qty'], False, 100.0)
    assert 'open' in e.trade.calls and e.state['lots']


# ------------------------------------------------------------------ group 5: leverage brackets
def test_g5_bracket_maintenance_at_exact_boundaries_and_tiers():
    m = E.bracket_maint
    assert m(BRK, 0) == 0 and m(BRK, 10000) == pytest.approx(40.0)
    assert m(BRK, 49999.99) == pytest.approx(49999.99 * 0.004)
    assert m(BRK, 50000) == pytest.approx(200.0) and 50000 * 0.005 - 50 == pytest.approx(200.0)   # continuous at the boundary
    assert m(BRK, 50000.01) == pytest.approx(50000.01 * 0.005 - 50)
    assert m(BRK, 250000) == pytest.approx(1200.0) and 250000 * 0.005 - 50 == pytest.approx(1200.0) and m(BRK, 1e6) == pytest.approx(1e6 * 0.01 - 1300)
    with pytest.raises(ValueError): m(BRK, 1e9)                                  # beyond the schedule: fail closed
    with pytest.raises(ValueError): m(BRK, float('nan'))


def test_g5_entry_that_crosses_a_tier_uses_the_higher_tier_and_cum():
    e = refusing(bal=1e6)
    add_lot(e, sym='BTCUSDT', qty=499.0, avg=100.0, stop=99.0)                   # 49 900 USDT on BTC (tier 1)
    ok, why, n = check(e, notional=1000.0, risk=10.0)
    assert ok, why
    worst = 49900 + 1000 + 10 * 1.5                                              # aggregate post-entry symbol notional
    assert n['maint_after_stops'] == pytest.approx((worst * 0.005 - 50) - 49900 * 0.004, abs=0.01)
    assert n['maint_after_stops'] != pytest.approx((1000 + 15) * 0.004), 'not the first tier rate'


def test_g5_aggregate_both_hedge_sides_cross_a_tier_into_a_steep_bracket():
    steep = [dict(floor=0.0, cap=1000.0, mmr=0.01, cum=0.0, lev=50.0), dict(floor=1000.0, cap=1e9, mmr=0.4, cum=390.0, lev=1.0)]
    e = refusing(bal=600.0); e.trade.brk['BTCUSDT'] = steep
    add_lot(e, sym='BTCUSDT', side='LONG', qty=5.0, avg=100.0, stop=99.0)
    add_lot(e, sym='BTCUSDT', side='SHORT', qty=4.0, avg=100.0, stop=101.0)     # 900 both sides: tier 1
    ok, why, n = check(e, notional=900.0, risk=5.0)                              # 1800+ aggregate: tier 2 at 40%
    assert not ok and n['check'] == 'worst_margin_ratio', why
    worst = 900 + 4 * 1 * 1.5 + 900 + 5 * 1.5                                    # short grows to its stop, plus the entry
    assert n['maint_after_stops'] == pytest.approx((worst * 0.4 - 390) - 900 * 0.01, abs=0.01)


def test_g5_brackets_are_cached_and_refetched_after_the_ttl(monkeypatch):
    e = refusing()
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    assert check(e)[0] and check(e)[0]
    assert e.trade.calls.count('brackets') == 1
    t[0] += E.LEV_BRACKET_TTL_S
    e.trade.brk['BTCUSDT'] = BC.BinanceError(-1000, 'down')
    ok, why, n = check(e)
    assert not ok and n['check'] == 'brackets', 'a schedule past its TTL is never used once the refresh fails'


@pytest.mark.parametrize('bad', [[], [dict(BRK[0], floor=10.0)] + BRK[1:], [BRK[0], dict(BRK[1], floor=60000.0)] + BRK[2:],
                                 [BRK[0], dict(BRK[1], cum=0.0)] + BRK[2:], [dict(BRK[0], mmr=float('nan'))] + BRK[1:],
                                 [dict(BRK[0], mmr=0.0)] + BRK[1:], [dict(BRK[0], lev=0.0)] + BRK[1:],
                                 [BRK[0], dict(BRK[1], mmr=0.003, cum=0.0)] + BRK[2:], [{'floor': 0}]])
def test_g5_missing_or_malformed_brackets_reject(bad):
    e = refusing(); e.trade.brk['BTCUSDT'] = bad
    ok, why, n = check(e)
    assert not ok and n['check'] == 'brackets', why
    assert 'BTCUSDT' not in e._brk, 'a malformed schedule is never cached'


def test_g5_client_reads_the_full_bracket_schedule():
    data = [dict(symbol='SOLUSDT', brackets=[dict(bracket=2, initialLeverage=10, notionalCap=250000, notionalFloor=50000, maintMarginRatio=0.025, cum=750),
                                            dict(bracket=1, initialLeverage=20, notionalCap=50000, notionalFloor=0, maintMarginRatio=0.01, cum=0)])]
    c, calls = client_with([Resp(200, data)])
    b = c.leverage_brackets('SOLUSDT')
    assert [x['floor'] for x in b] == [0.0, 50000.0] and b[1]['cum'] == 750.0 and b[0]['lev'] == 20.0
    assert E.check_brackets(b) is b and calls[0][2]['symbol'] == 'SOLUSDT'
    c, _ = client_with([Resp(200, [])])
    with pytest.raises(ValueError): c.leverage_brackets('SOLUSDT')


def test_g5_coin_maximum_comes_from_the_cached_schedule():
    e = refusing(cur=20, mtype='ISOLATED'); e.trade.brk['BTCUSDT'] = [dict(BRK[0], lev=8.0)] + [dict(x, lev=min(x['lev'], 8.0)) for x in BRK[1:]]
    r = entry_skipped(e)
    assert r['cap'] == 8 and 'above the 8x cap' in e.last_skip


# ------------------------------------------------------------------ group 6: non-finite / invalid numbers never pass
@pytest.mark.parametrize('field,val', [('totalMaintMargin', 'NaN'), ('totalMaintMargin', 'inf'), ('totalMaintMargin', '-5'), ('totalMaintMargin', '-0.1'),
                                       ('totalMaintMargin', None), ('totalMarginBalance', '0'), ('totalMarginBalance', '-100'),
                                       ('totalMarginBalance', 'nan'), ('totalMarginBalance', 'Infinity'), ('totalMarginBalance', 'abc')])
def test_g6_invalid_account_numbers_reject(field, val):
    e = refusing(); eq = e.equity(); e.trade.acc[field] = val
    ok, why, n = check(e)
    assert not ok and n['check'] == 'numeric', why
    r = entry_skipped(e, eq=eq)
    assert r['reason'] == 'exposure_rejected' and r['exposure']['check'] == 'numeric'
    json.dumps(e.lev_refusals, allow_nan=False)                                  # status JSON stays valid


@pytest.mark.parametrize('field,val', [('qty', float('nan')), ('qty', -1.0), ('qty', 0.0), ('avg', float('inf')), ('avg', -50.0),
                                       ('q0', float('nan')), ('levels', [float('nan')]), ('w', ['x'])])
def test_g6_invalid_lot_numbers_reject(field, val):
    e = refusing()
    k, lot = add_lot(e, qty=1.0, levels=[45.0], w=[1.0], q0=1.0)
    lot[field] = val
    if field == 'qty' and val == val and val > 0: e.trade.pos[('ETHUSDT', 'LONG')] = val
    ok, why, n = check(e)
    assert not ok, (field, val)
    assert n['check'] in ('numeric', 'reconcile'), why


@pytest.mark.parametrize('n_, r_', [(float('nan'), 5.0), (float('inf'), 5.0), (-1.0, 5.0), (0.0, 5.0), (100.0, float('nan')),
                                    (100.0, -1.0), (None, 5.0), (100.0, None)])
def test_g6_invalid_entry_size_rejects(n_, r_):
    e = refusing()
    ok, why, n = check(e, notional=n_, risk=r_)
    assert not ok and n['check'] in ('numeric', 'entry_size'), why


@pytest.mark.parametrize('row', [dict(mark=float('nan')), dict(mark=0.0), dict(notional=float('nan')), dict(qty=float('nan')),
                                 dict(qty=-1.0), dict(side='BOTH')])
def test_g6_invalid_position_rows_reject(row):
    e = refusing(); add_lot(e)
    real = e.trade.position_risk
    e.trade.position_risk = lambda: [dict(r, **row) for r in real()]
    ok, why, n = check(e)
    assert not ok and n['check'] in ('numeric', 'snapshot'), why


@pytest.mark.parametrize('cur', [float('nan'), 0, -5, 'x', float('inf')])
def test_g6_invalid_current_leverage_is_unknown(cur):
    e = refusing(cur=cur)
    r = entry_skipped(e, 'current leverage unknown')
    assert r['reason'] == 'leverage_unknown' and r['current'] is None
    json.dumps(e.lev_refusals, allow_nan=False)


def test_g6_invalid_cap_or_coin_maximum_never_passes():
    e = refusing(); e.trade.brk['BTCUSDT'] = [dict(BRK[0], lev=float('nan'))] + BRK[1:]
    assert e._lev_max('BTCUSDT') is None
    e.S['MAX_LEVERAGE'] = float('nan')
    ok, why, n = e._exposure_check('BTCUSDT', 100.0, 5.0)
    assert not ok and n['check'] == 'numeric'


def test_g6_status_json_is_valid_on_every_path():
    e = refusing(mm=1e9); entry_skipped(e)                                        # huge ratio
    e2 = refusing(); add_lot(e2, qty=400.0, avg=30.0, stop=31.0); e2.trade.acc['totalMaintMargin'] = '200'; entry_skipped(e2)  # negative balance
    for x in (e, e2):
        s = json.dumps(x.lev_refusals, allow_nan=False)
        assert 'NaN' not in s and 'Infinity' not in s


# ------------------------------------------------------------------ group 7: telemetry and the panel
def test_g7_every_path_records_outcome_reason_and_counts(monkeypatch):
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    paths = {}
    e = refusing(cur=5); opened(e); paths['within_cap'] = dict(e.lev_refusals['BTCUSDT'])
    e = refusing(cur=None); entry_skipped(e); paths['leverage_unknown'] = dict(e.lev_refusals['BTCUSDT'])
    e = refusing(mtype='ISOLATED'); entry_skipped(e); paths['margin_isolated'] = dict(e.lev_refusals['BTCUSDT'])
    e = refusing(mtype=None); entry_skipped(e); paths['margin_unknown'] = dict(e.lev_refusals['BTCUSDT'])
    e = refusing(); k, lot = add_lot(e); lot['stop_dirty'] = True; entry_skipped(e); paths['exposure_rejected'] = dict(e.lev_refusals['BTCUSDT'])
    e = refusing(); opened(e); paths['exposure_ok'] = dict(e.lev_refusals['BTCUSDT'])
    for reason, r in paths.items():
        assert r['reason'] == reason and r['outcome'] == ('went_ahead' if reason in ('within_cap', 'exposure_ok') else 'skipped')
        assert r['detail'] and r['via'] == 'refused' and r['api_refusals'] == 1 and r['cooldown_checks'] == 0
        assert 'injected' in r['last_api_error'] and r['count'] == r['proceeded'] + r['skipped']
    assert paths['exposure_rejected']['exposure']['check'] == 'stops'
    assert paths['leverage_unknown']['exposure'] is None and paths['margin_isolated']['margin_type'] == 'ISOLATED'


def test_g7_cooldown_decides_before_any_venue_write_or_bracket_fetch(monkeypatch):
    e = refusing()
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    opened(e); e.close_lot(next(iter(e.state['lots'])), 'signal', mark=100.0)
    assert e.trade.calls.count('margin') == 1 and e.trade.calls.count('brackets') == 1
    del e.trade.calls[:]
    t[0] += 60; opened(e)
    assert 'margin' not in e.trade.calls and 'leverage' not in e.trade.calls and 'brackets' not in e.trade.calls
    assert {'account', 'positions_all', 'orders_all', 'mstate'} <= set(e.trade.calls), 'fresh safety reads still happen'
    r = e.lev_refusals['BTCUSDT']
    assert (r['via'], r['cooldown_checks'], r['api_refusals'], r['outcome']) == ('cooldown', 1, 1, 'went_ahead')
    assert r['cooldown_left'] == E.LEV_REFUSAL_COOLDOWN_S - 60 and 'injected' in r['last_api_error']


def _panel_src():
    return open(os.path.join(ROOT, 'panel.html'), encoding='utf-8').read()


def test_g7_panel_dialog_source():
    src = _panel_src()
    dlg = src[src.index('const LEVR={'):src.index('function orphDlg(){')]
    for code in ('within_cap', 'exposure_ok', 'leverage_unknown', 'margin_isolated', 'margin_unknown', 'exposure_rejected'):
        assert code + ':' in dlg
    assert 'api_refusals' in dlg and 'cooldown_checks' in dlg and "v.via==='cooldown'" in dlg
    assert 'esc(x.why' in dlg and 'esc(x.check' in dlg and 'esc(v.detail)' in dlg and 'esc(v.last_api_error' in dlg
    assert 'Trade sizes never exceed your cap' in dlg and 'No trade ever runs above your cap' not in dlg
    assert 'adds and the maker' in dlg


@pytest.mark.skipif(not shutil.which('node'), reason='node not installed')
def test_g7_panel_renders_every_path_escaped():
    src = _panel_src()
    js = src[src.index('const esc='):].split('\n', 1)[0] + '\n' + src[src.index('const LEVR={'):src.index('function orphDlg(){')]
    evil = '<img src=x onerror=alert(1)>'
    rows = dict(
        A=dict(count=1, proceeded=1, skipped=0, api_refusals=1, cooldown_checks=0, current=5, cap=10, reason='within_cap', outcome='went_ahead', via='refused'),
        B=dict(count=1, skipped=1, current=None, cap=10, reason='leverage_unknown', outcome='skipped', detail=evil, via='refused', last_api_error=evil),
        C=dict(count=1, skipped=1, current=20, cap=10, reason='margin_isolated', outcome='skipped', margin_type='ISOLATED', detail='isolated ' + evil),
        D=dict(count=1, skipped=1, current=20, cap=10, reason='margin_unknown', outcome='skipped', detail='unknown'),
        E=dict(count=2, skipped=1, current=20, cap=10, reason='exposure_rejected', outcome='skipped', via='cooldown', cooldown_left=1740,
               exposure=dict(ok=False, check='stops' + evil, why='ETH stop ' + evil)),
        F=dict(count=1, proceeded=1, current=20, cap=10, reason='exposure_ok', outcome='went_ahead', accepted='exposure', margin_type='CROSSED',
               exposure=dict(ok=True, effective_leverage=1.2, worst_margin_ratio=0.031)),
        G=dict(count=3, proceeded=0, skipped=3, current=20, cap=10, accepted=None, exposure=None))                 # older record
    harness = js + '\nlet out="";const fT=s=>String(s||"-");const info=(t,h)=>{out=h};const D={health:{lev_refusals:' + json.dumps(rows) + \
              '}};levDlg();process.stdout.write(out);'
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f: f.write(harness)
    try: html = subprocess.run(['node', f.name], capture_output=True, text=True, timeout=30, check=True).stdout
    finally: os.unlink(f.name)
    assert '<img' not in html and '&lt;img src=x onerror=alert(1)&gt;' in html
    for txt in ('already within the cap', 'current leverage unknown', 'isolated margin', 'margin type unknown',
                'exposure proof failed (check: stops', 'cooldown: no new request to Binance for 1740s',
                'cross-margin exposure proof passed, cross margin, account leverage 1.2x, worst-case margin ratio 3%', '>skipped<'):
        assert txt in html, txt
    assert '1 / 0' in html and '3 / 0' in html                                  # Binance refusals / cooldown checks
