"""T03c: automatic leverage handling. When Binance refuses to lower a coin's leverage and the coin stays above the cap, an
entry may still go ahead - but only under CROSS margin, with the account's effective leverage within the cap and the
worst case (every stop filled) far from liquidation. Every unknown answer keeps the T03a skip. No new order paths."""
import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import mk_engine, SL, SG, opened, client_with, Resp     # noqa: E402
import engine as E                                                      # noqa: E402
import binance_client as BC                                             # noqa: E402


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(E.time, 'sleep', lambda s: None)


def refusing(cur=20, mtype='CROSSED', bal=5000.0, mm=0.0, **S):
    """Binance refuses every leverage change; the coin stays at `cur`x with margin type `mtype`."""
    e, _ = mk_engine(MAX_LEVERAGE=10, **S)
    e.trade.fail.add('leverage')
    e.trade.margin_state = lambda s: (e.trade.calls.append('mstate'), dict(leverage=cur, margin_type=mtype))[1]
    e.trade.account = lambda: {'totalMarginBalance': str(bal), 'totalMaintMargin': str(mm), 'totalUnrealizedProfit': '0'}
    return e


def lev_posts(e): return sum(1 for c in e.trade.calls if c == 'leverage')


def test_cross_margin_above_cap_enters_when_the_exposure_is_safe():
    e = refusing()
    k = opened(e)
    lot = e.state['lots'][k]
    assert lot['stop_id'] in e.trade.stops                                       # entry with its exchange stop, as always
    r = e.lev_refusals['BTCUSDT']
    assert (r['count'], r['proceeded'], r['by_exposure'], r['skipped'], r['accepted']) == (1, 1, 1, 0, 'exposure')
    assert r['current'] == 20 and r['margin_type'] == 'CROSSED' and r['exposure']['ok'] is True
    assert r['exposure']['effective_leverage'] <= 10 and r['exposure']['worst_margin_ratio'] <= 0.5
    assert 'BTCUSDT' not in e._lev, 'never cached: the exchange is still above the cap'


def test_the_entry_size_reaches_the_check():
    e = refusing(); seen = {}
    real = e._exposure_check
    e._exposure_check = lambda sym, n, r, mx: (seen.update(n=n, r=r), real(sym, n, r, mx))[1]
    k = opened(e); lot = e.state['lots'][k]
    assert abs(seen['n'] - lot['qty'] * 100.0) < 1e-6 and seen['r'] > 0


@pytest.mark.parametrize('mtype', ['ISOLATED', None])
def test_isolated_or_unknown_margin_type_skips(mtype):
    e = refusing(mtype=mtype)
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'not cross' in e.last_skip and 'open' not in e.trade.calls and not e.state['lots']
    assert e.lev_refusals['BTCUSDT']['skipped'] == 1 and e.lev_refusals['BTCUSDT']['by_exposure'] == 0


def test_missing_account_margin_data_skips():
    e = refusing()
    e.trade.account = lambda: {'totalMarginBalance': '5000'}                     # no totalMaintMargin
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'account margin data unavailable' in e.last_skip and 'open' not in e.trade.calls


def test_account_effective_leverage_above_the_cap_skips():
    e = refusing()
    e.state['lots']['x'] = dict(symbol='ETHUSDT', side='LONG', qty=1000.0, avg=50.0, stop=49.99, sleeve='Z')   # 50 000 USDT open
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'effective leverage' in e.last_skip and 'open' not in e.trade.calls
    assert e.lev_refusals['BTCUSDT']['exposure']['ok'] is False


def test_worst_case_margin_ratio_above_the_limit_skips():
    e = refusing(mm=2600.0)                                                      # maintenance margin already > 50% of 5000
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'worst-case margin ratio' in e.last_skip and 'open' not in e.trade.calls


def test_open_risk_of_existing_lots_counts_in_the_worst_case():
    e = refusing(bal=5000.0, mm=2000.0)
    e.state['lots']['x'] = dict(symbol='ETHUSDT', side='LONG', qty=100.0, avg=50.0, stop=40.0, sleeve='Z')   # 1000 USDT at risk
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'worst-case margin ratio' in e.last_skip, e.last_skip                 # 2000 / (5000 - 1000 - ...) > 50%


def test_an_open_lot_without_a_stop_makes_the_check_fail_closed():
    e = refusing()
    for bad in (None, 0, 'x'):
        e.state['lots']['x'] = dict(symbol='ETHUSDT', side='LONG', qty=1.0, avg=50.0, stop=bad, sleeve='T')
        ok, why, _ = e._exposure_check('BTCUSDT', 100.0, 5.0, 125)
        assert not ok and why == 'an open lot has no known stop', bad


def test_an_exception_in_the_check_skips():
    e = refusing()
    def boom(): raise RuntimeError('account endpoint down')
    real = e._exposure_check
    def check(*a):
        e.trade.account = boom                                                   # the account read fails inside the check
        return real(*a)
    e._exposure_check = check
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'exposure check failed' in e.last_skip and 'open' not in e.trade.calls


def test_unknown_current_leverage_still_skips():
    e = refusing(cur=None)
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'current leverage unknown' in e.last_skip and 'open' not in e.trade.calls


def test_within_cap_path_is_unchanged_and_needs_no_exposure_check():
    e = refusing(cur=5, mtype='ISOLATED')
    e._exposure_check = lambda *a: pytest.fail('not needed when the coin is already within the cap')
    opened(e)
    assert e.lev_refusals['BTCUSDT']['accepted'] == 'within_cap' and e.lev_refusals['BTCUSDT']['by_exposure'] == 0


def test_a_refusal_starts_a_cooldown_without_new_change_requests(monkeypatch):
    e = refusing()
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    opened(e)
    assert lev_posts(e) == 2                                                     # first try + one retry
    e.close_lot(next(iter(e.state['lots'])), 'signal', mark=100.0)
    t[0] += 60; opened(e)
    assert lev_posts(e) == 2, 'inside the cooldown: no new leverage change request'
    assert e.lev_refusals['BTCUSDT']['count'] == 2 and 'refused recently' in e.lev_refusals['BTCUSDT']['last_error']
    e.close_lot(next(iter(e.state['lots'])), 'signal', mark=100.0)
    t[0] += E.LEV_REFUSAL_COOLDOWN_S; opened(e)
    assert lev_posts(e) == 4, 'after the cooldown the change is requested again'


def test_cooldown_still_applies_every_safety_check(monkeypatch):
    e = refusing()
    opened(e); e.close_lot(next(iter(e.state['lots'])), 'signal', mark=100.0)
    e.trade.margin_state = lambda s: dict(leverage=20, margin_type='ISOLATED')
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()) and 'not cross' in e.last_skip


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


def test_panel_explains_the_decision_per_coin():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'panel.html'), encoding='utf-8').read()
    dlg = src[src.index('function levDlg(){'):src.index('function orphDlg(){')]
    assert "v.accepted==='exposure'" in dlg and "v.accepted==='within_cap'" in dlg and 'cross margin' in dlg
    assert 'Trade sizes never exceed your cap' in dlg and 'No trade ever runs above your cap' not in dlg
    assert 'esc(v.exposure.why)' in dlg                                          # Binance/engine text is escaped
