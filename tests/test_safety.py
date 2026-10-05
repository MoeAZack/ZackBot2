"""ZackBot v3 safety tests: every fix from the v2.2 audits gets a test that tries to break it.
Run:  python -m pytest -q tests/   (from the zackbot2 folder)"""
import json, os, sys, tempfile, types, logging
from datetime import datetime, timezone, timedelta
import pytest, pandas as pd, numpy as np, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp()
import binance_client as BC
import engine as E
import backtest as B


# ------------------------------------------------------------------ a fake Binance with failure injection
class FakeX:
    def __init__(self, *a, **k):
        self.pos, self.stops, self.n, self.calls = {}, {}, 0, []
        self.mark = {'BTCUSDT': 100.0, 'ETHUSDT': 50.0}
        self.fail = set()          # names of methods that should raise
        self.balance = 5000.0
        self.offset = 0
    def _f(self, name, code=-1000):
        self.calls.append(name)
        if name in self.fail: raise BC.BinanceError(code, f'injected {name} failure')
    def sync_time(self): pass
    def exchange_info(self):
        return {'symbols': [{'symbol': s, 'contractType': 'PERPETUAL', 'status': 'TRADING', 'filters': [
            {'filterType': 'MARKET_LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001'}, {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
            {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]} for s in self.mark]}
    def marks(self): return dict(self.mark)
    def account(self): return {'totalMarginBalance': str(self.balance), 'totalUnrealizedProfit': '0'}
    def positions(self): self._f('positions'); return {k: v for k, v in self.pos.items() if v > 1e-12}
    def hedge_mode(self): return True
    def set_hedge_mode(self, on=True): pass
    def set_margin_type(self, *a): self._f('margin')
    def leverage_max(self, s): return 125
    def set_leverage(self, s, lev): self._f('leverage'); self.calls.append(('lev', s, lev))
    def open(self, s, ps, q):
        self._f('open'); self.pos[(s, ps)] = self.pos.get((s, ps), 0) + float(q); return {'avgPrice': str(self.mark[s]), 'executedQty': q}
    def close(self, s, ps, q):
        self._f('close'); self.pos[(s, ps)] = self.pos.get((s, ps), 0) - float(q); return {'avgPrice': str(self.mark[s])}
    def stop(self, s, ps, q, p):
        if 'stop_2021' in self.fail: self.calls.append('stop'); raise BC.BinanceError(-2021, 'Order would immediately trigger.')
        self._f('stop'); self.n += 1; tag = f'o:{self.n}'; self.stops[tag] = (s, ps, float(q), float(p)); return tag
    def cancel(self, s, tag): self._f('cancel'); self.stops.pop(tag, None); return True
    def open_stop_tags(self, s): self._f('tags'); return {k for k, v in self.stops.items() if v[0] == s}
    def premium(self, s): return {'lastFundingRate': '0.0001'}


def mk_engine(tmp=None, **S):
    tmp = tmp or tempfile.mkdtemp()
    E.Futures = FakeX
    e = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e.data = e.trade                                   # one fake for prices and trading
    e.S.update(dict(UNIVERSE=['BTCUSDT', 'ETHUSDT'], SYMBOLS_ON={'BTCUSDT': True, 'ETHUSDT': True}, CAPITAL_CAP=500.0), **S)
    e.connect()
    e.marks = e.trade.marks()
    return e, tmp


SL = E.sleeve('T', 'ema_mom', 0.5, 0.02, 2, 'all')
SG = dict(close=100.0, atr=2.0, time='2026-10-04 00:00:00', le=True, se=False, lx=False, sx=False)


def opened(e, sym='BTCUSDT', side='LONG'):
    assert e.open_lot(SL, sym, side, SG, None, e.equity()), e.last_skip
    return next(k for k, l in e.state['lots'].items() if l['symbol'] == sym and l['side'] == side)


# ------------------------------------------------------------------ exchange client: no double orders
class Resp:
    def __init__(self, code, data, headers=None): self.status_code, self._d, self.headers = code, data, headers or {}; self.content = b'x'
    def json(self): return self._d


def client_with(script):
    c = BC.Futures.__new__(BC.Futures)
    c.key, c.secret, c.base, c.rw, c.offset, c.last_ok = 'k', b's', BC.TESTNET, 6000, 0, 0
    calls = []
    def request(method, url, params=None, timeout=None):
        calls.append((method, url.split('/fapi')[1], dict(params or {})))
        r = script.pop(0)
        if isinstance(r, Exception): raise r
        return r
    c.s = types.SimpleNamespace(request=request, headers={}, get=lambda *a, **k: Resp(200, {'serverTime': 0}))
    c.sync_time = lambda: None
    return c, calls


def test_order_timeout_then_found_is_not_resent(monkeypatch):
    monkeypatch.setattr(BC.time, 'sleep', lambda x: None)
    c, calls = client_with([requests.Timeout('lost'), Resp(200, {'orderId': 7, 'status': 'FILLED', 'avgPrice': '100'})])
    o = c.open('BTCUSDT', 'LONG', '0.01')
    assert o['orderId'] == 7
    assert [x[0] for x in calls] == ['POST', 'GET']                      # looked up, never posted twice
    assert calls[1][2]['origClientOrderId'] == calls[0][2]['newClientOrderId']


def test_order_timeout_never_arrived_is_sent_once_more_with_same_id(monkeypatch):
    monkeypatch.setattr(BC.time, 'sleep', lambda x: None)
    c, calls = client_with([requests.ConnectionError('x'), Resp(400, {'code': -2013, 'msg': 'Order does not exist.'}),
                            Resp(200, {'orderId': 9, 'avgPrice': '100'})])
    assert c.open('BTCUSDT', 'LONG', '0.01')['orderId'] == 9
    assert [x[0] for x in calls] == ['POST', 'GET', 'POST']
    assert calls[0][2]['newClientOrderId'] == calls[2][2]['newClientOrderId']


def test_order_5xx_is_ambiguous_not_retried_blindly(monkeypatch):
    monkeypatch.setattr(BC.time, 'sleep', lambda x: None)
    c, calls = client_with([Resp(503, {'code': -1001, 'msg': 'busy'}), Resp(200, {'orderId': 3, 'status': 'FILLED'})])
    assert c.open('BTCUSDT', 'LONG', '0.01')['orderId'] == 3
    assert sum(1 for x in calls if x[0] == 'POST') == 1


def test_get_retries_429_with_backoff(monkeypatch):
    slept = []; monkeypatch.setattr(BC.time, 'sleep', lambda x: slept.append(x))
    c, calls = client_with([Resp(429, {'code': -1003, 'msg': 'too many'}, {'Retry-After': '2'}), Resp(200, [1, 2])])
    assert c._req('GET', '/fapi/v1/klines') == [1, 2] and slept == [2.0]


def test_signed_retry_is_resigned(monkeypatch):
    monkeypatch.setattr(BC.time, 'sleep', lambda x: None)
    c, calls = client_with([Resp(400, {'code': -1021, 'msg': 'ts'}), Resp(200, {'ok': 1})])
    c._req('GET', '/fapi/v2/account', signed=True)
    assert len(calls) == 2 and 'signature' in calls[1][2]


def test_cancel_unknown_order_is_ok_but_rate_limit_raises(monkeypatch):
    monkeypatch.setattr(BC.time, 'sleep', lambda x: None)
    c, _ = client_with([Resp(400, {'code': -2011, 'msg': 'Unknown order sent.'})])
    assert c.cancel('BTCUSDT', 'o:1') is True
    c, _ = client_with([Resp(429, {'code': -1003, 'msg': 'x'})] * 4)
    with pytest.raises(BC.BinanceError): c.cancel('BTCUSDT', 'o:1')


# ------------------------------------------------------------------ order lifecycle
def test_failed_close_keeps_the_stop():
    e, _ = mk_engine(); k = opened(e); tag = e.state['lots'][k]['stop_id']
    e.trade.fail.add('close')
    with pytest.raises(BC.BinanceError): e.close_lot(k, 'exit_signal')
    assert k in e.state['lots'] and tag in e.trade.stops                    # stop still protects the position


def test_close_cancels_stop_after_success():
    e, _ = mk_engine(); k = opened(e); tag = e.state['lots'][k]['stop_id']
    e.close_lot(k, 'exit_signal')
    assert k not in e.state['lots'] and tag not in e.trade.stops and not e.trade.pos.get(('BTCUSDT', 'LONG'))


def test_stop_update_failure_keeps_old_stop_and_retries():
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]; old = (lot['stop'], lot['stop_id'])
    e.trade.fail.add('stop')
    assert e._replace_stop(lot, lot['stop'] + 1) is False
    assert (lot['stop'], lot['stop_id']) == old and lot['stop_dirty']       # engine still knows the REAL stop
    e.trade.fail.clear(); e.manage(e.trade.marks())
    assert not lot['stop_dirty'] and lot['stop_id'] != old[1] and old[1] not in e.trade.stops


def test_stop_that_would_trigger_closes_the_trade():
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]
    e.trade.fail.add('stop_2021')
    e._replace_stop(lot, 101.0)
    e.trade.fail.clear(); e.manage(e.trade.marks())
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'stop_crossed'


def test_cancel_failure_is_parked_and_retried():
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]; old = lot['stop_id']
    e.trade.fail.add('cancel'); e._replace_stop(lot, lot['stop'] + 0.5)
    assert [lot['symbol'], old] in e.state['orphans'] and old in e.trade.stops
    e.trade.fail.clear(); e.manage(e.trade.marks())
    assert e.state['orphans'] == [] and old not in e.trade.stops


def test_stop_and_safety_close_both_fail_position_stays_tracked():
    e, _ = mk_engine(); e.trade.fail.update({'stop', 'close'})
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    lot = next(iter(e.state['lots'].values()))
    assert lot['stop_dirty'] and not lot['stop_id']                         # tracked as unprotected, not forgotten
    assert e.entry_block(SL, 'BTCUSDT', 'LONG')                             # and blocks new entries on that coin/side
    e.trade.fail.clear(); e.manage(e.trade.marks())
    assert lot['stop_id'] and not lot['stop_dirty']


def test_no_wrong_lot_guessing_when_stop_list_unavailable():
    e, _ = mk_engine(); k = opened(e)
    e.trade.pos[('BTCUSDT', 'LONG')] = 0; e.trade.fail.add('tags')
    e.reconcile(500)
    assert k in e.state['lots']                                             # nothing guessed; retried later
    e.trade.fail.clear(); e.trade.stops.clear(); e.reconcile(500)
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'stop'


def test_untracked_position_is_flagged_and_blocks_entries():
    e, _ = mk_engine(); e.trade.pos[('ETHUSDT', 'SHORT')] = 3.0
    e.reconcile(500); assert not e.untracked                                 # one reading could be lag
    e.reconcile(500)
    assert 'ETHUSDT|SHORT' in e.untracked
    assert 'untracked' in (e.entry_block(SL, 'ETHUSDT', 'SHORT') or '')
    k = opened(e); e.trade.pos[('BTCUSDT', 'LONG')] += 1.0; e.reconcile(500); e.reconcile(500)
    assert 'BTCUSDT|LONG' in e.untracked                                    # extra size on a tracked coin too


def test_flatten_reports_failures():
    e, _ = mk_engine(); opened(e); opened(e, 'ETHUSDT')
    e.trade.fail.add('close'); r = e.flatten()
    assert len(r['failed']) == 2 and r['closed'] == [] and e.S['ENTRIES_PAUSED']


# ------------------------------------------------------------------ one entry gate for every path
def test_take_signal_respects_pause_halt_coin_and_max(monkeypatch):
    e, _ = mk_engine(); e.S['SLEEVES'] = [SL]; e.signals = {'T|BTCUSDT': SG, 'T|ETHUSDT': SG}
    monkeypatch.setattr(e, 'candles', lambda s, tf: None)
    e.S['ENTRIES_PAUSED'] = True
    with pytest.raises(ValueError, match='paused'): e.take_signal('T', 'BTCUSDT')
    e.S['ENTRIES_PAUSED'] = False; e.state['halted'] = True
    with pytest.raises(ValueError, match='halt'): e.take_signal('T', 'BTCUSDT')
    e.state['halted'] = False; e.S['SYMBOLS_ON']['BTCUSDT'] = False
    with pytest.raises(ValueError, match='switched off'): e.take_signal('T', 'BTCUSDT')
    e.S['SYMBOLS_ON']['BTCUSDT'] = True; sl1 = dict(SL, max_pos=1); e.S['SLEEVES'] = [sl1]
    assert e.take_signal('T', 'BTCUSDT')
    with pytest.raises(ValueError, match='max positions'): e.take_signal('T', 'ETHUSDT')


def test_manual_trade_limits(monkeypatch):
    e, _ = mk_engine()
    monkeypatch.setattr(e, 'candles', lambda s, tf: pd.DataFrame(dict(c=[100.0], atr=[2.0])))
    with pytest.raises(ValueError, match='between 0 and 5%'): e.manual_trade('BTCUSDT', 'LONG', 0.5, 2.5)
    with pytest.raises(ValueError): e.manual_trade('BTCUSDT', 'LONG', 0.01, 0.1)
    e.state['halted'] = True
    with pytest.raises(ValueError, match='halt'): e.manual_trade('BTCUSDT', 'LONG', 0.01, 2.5)
    e.state['halted'] = False
    assert e.manual_trade('BTCUSDT', 'LONG', 0.05, 0.5)
    lot = next(iter(e.state['lots'].values()))
    assert lot['qty'] * lot['avg'] <= e.S['MAX_LEVERAGE'] * e.last_eq + 1    # leverage cap applies to manual trades


def test_exchange_leverage_follows_setting():
    e, _ = mk_engine(MAX_LEVERAGE=3); opened(e)
    assert ('lev', 'BTCUSDT', 3) in e.trade.calls


def test_leverage_failure_blocks_entry():
    e, _ = mk_engine(); e.trade.fail.add('leverage')
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()) and 'leverage' in e.last_skip and not e.state['lots']


# ------------------------------------------------------------------ capital, guards, restarts
def hist(e, pnl, when=None):
    e.history.append(dict(id=str(len(e.history)), pnl=pnl, closed=(when or E.now_utc()).isoformat(timespec='seconds'), symbol='BTCUSDT', side='LONG'))


def test_fixed_mode_losses_are_seen_by_the_guards():
    e, _ = mk_engine(DAILY_LOSS_HALT=0.08); e.S['CAP_SINCE'] = (E.now_utc() - timedelta(hours=1)).isoformat(timespec='seconds')
    e.equity(); e.check_guards(); assert e.guard_eq == 500
    hist(e, -45); e.equity(); e.check_guards()
    assert e.last_eq == 500 and e.guard_eq == 455 and e.state['halted']         # sized on 500, but the 9% loss halts


def test_withdrawal_is_not_a_loss_and_deposit_not_a_gain():
    e, _ = mk_engine(DAILY_LOSS_HALT=0.08, PEAK_DD_FLATTEN=0.2); e.equity(); e.check_guards()
    e.capital_action('withdraw', 200); e.equity(); e.check_guards()
    assert not e.state['halted'] and abs(e.guard_eq - 300) < 1e-6 and abs(e.state['day_start_equity'] - 300) < 1e-6
    e.capital_action('deposit', 100); e.equity(); e.check_guards()
    assert abs(e.state['peak_equity'] - 400) < 1e-6 and not e.state['halted']


def test_lowering_start_amount_does_not_trigger_flatten():
    e, _ = mk_engine(PEAK_DD_FLATTEN=0.2); opened(e); e.equity(); e.check_guards()
    e.set_capital_base(300); e.equity(); e.check_guards()
    assert e.state['lots'] and not e.S['ENTRIES_PAUSED']


def test_compound_sizing_uses_closed_pnl_only():
    e, _ = mk_engine(); e.S['CAP_SINCE'] = (E.now_utc() - timedelta(hours=1)).isoformat(timespec='seconds')
    e.S['COMPOUND'] = True; hist(e, 100); e.equity()
    assert e.last_eq == 600


def test_daily_halt_survives_restart():
    e, tmp = mk_engine(); e.equity(); e.check_guards(); e.state['halted'] = True; e.save_state()
    e2, _ = mk_engine(tmp); e2.equity(); e2.check_guards()
    assert e2.state['halted'] and e2.state['day'] == E.trading_day()


def test_drawdown_flatten_resets_peak_once():
    e, _ = mk_engine(PEAK_DD_FLATTEN=0.1); opened(e); e.equity(); e.check_guards()
    hist(e, -60); e.equity(); e.check_guards()
    assert not e.state['lots'] and e.S['ENTRIES_PAUSED'] and e.state['peak_equity'] == e.guard_eq
    e.S['ENTRIES_PAUSED'] = False; e.check_guards()
    assert not e.S['ENTRIES_PAUSED']                                             # no repeated re-pause


def test_cairo_day_and_dst_fallback():
    assert E._egypt_offset(datetime(2026, 7, 1)) == timedelta(hours=3)
    assert E._egypt_offset(datetime(2026, 12, 1)) == timedelta(hours=2)
    assert E._egypt_offset(datetime(2026, 10, 29)) == timedelta(hours=3) and E._egypt_offset(datetime(2026, 10, 30)) == timedelta(hours=2)
    assert len(E.trading_day()) == 10 and E.next_reset_utc() > E.now_utc().isoformat()


def test_orphan_lot_still_gets_exit_signals():
    e, _ = mk_engine(); k = opened(e); e.S['SLEEVES'] = []           # its slot was removed by a profile change
    ex = e._orphan_sleeves('4h')
    assert ex and ex[0]['id'] == 'T' and ex[0]['key'] == 'ema_mom'


def test_history_keeps_original_entry_price():
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]; lot['e0'] = 123.0
    e.close_lot(k, 'exit_signal'); assert e.history[-1]['entry'] == 100.0


# ------------------------------------------------------------------ app: config, validation, secrets
@pytest.fixture(scope='module')
def app():
    import app as A
    return A


def test_config_rejects_newline_injection(app):
    with pytest.raises(ValueError): app.write_cfg({'API_KEY': 'abc\nMODE=live\nLIVE_CONFIRM=YES_REAL_MONEY'})
    app.write_cfg({'API_KEY': 'A' * 64, 'API_SECRET': 'B' * 64})
    with open(app.config_path(), 'a') as f: f.write('MODE=live\nLIVE_CONFIRM=YES_REAL_MONEY\n')   # appended by hand
    assert app.load_cfg()['MODE'] == 'paper'                                  # first value wins


def test_backtest_ids_are_validated(app):
    assert not app.BT_ID.match('../state') and not app.BT_ID.match('..\\..\\settings')
    assert app.BT_ID.match('20261004-122247-f5db') and app.BT_ID.match('20261004-122247-study-boost_active')


def test_mgmt_and_symbols_validated(app):
    with pytest.raises(ValueError): app.clean_symbols(['BTC<img src=x onerror=alert(1)>USDT'])
    with pytest.raises(ValueError): app.clean_mgmt({'evil': 1})
    with pytest.raises(ValueError): app.clean_mgmt({'stop_atr': 'x'})
    assert app.clean_mgmt({'runner': {'be_r': 2, 'giveback': 0.5}, 'tp1_r': 3}) == {'runner': {'be_r': 2.0, 'giveback': 0.5}, 'tp1_r': 3.0}
    with pytest.raises(ValueError): app.validate_sleeve(dict(id='MAN', key='ema_mom', share=.5, risk=.02, max_pos=2), 0)


def test_secrets_scrubbed_from_logs(app):
    rec = logging.LogRecord('zackbot', 30, __file__, 1, 'telegram: https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendMessage', (), None)
    app.Scrub().filter(rec); assert 'AAHdq' not in rec.getMessage()
    app.SECRETS.add('SuperSecretApiKey123'); rec = logging.LogRecord('z', 30, __file__, 1, 'key SuperSecretApiKey123 used', (), None)
    app.Scrub().filter(rec); assert 'SuperSecret' not in rec.getMessage()


def test_icon_rejects_non_images(app):
    assert app._img_type(b'<svg onload=alert(1)>') is None and app._img_type(b'\x89PNG\r\n\x1a\n....') == 'image/png'
    assert not app.ICON_HOSTS.match('https://evil.com/x.png') and app.ICON_HOSTS.match('https://bin.bnbstatic.com/x.png')


# ------------------------------------------------------------------ backtester
def test_book_aligns_on_timestamps():
    t = pd.date_range('2026-01-01', periods=300, freq='4h')
    a = pd.DataFrame(dict(t=t, o=1.0, h=1.0, l=1.0, c=np.arange(300.0), v=1.0))
    b = a.drop(index=150).reset_index(drop=True)                             # one missing candle in coin B
    bk = B.Book({'BTCUSDT': a, 'XUSDT': b})
    assert bk.gaps == {'BTCUSDT': 1} or bk.gaps == {}                       # reported, never shifted
    assert (bk.d['BTCUSDT'].t.values == bk.d['XUSDT'].t.values).all()
    assert (bk.d['BTCUSDT'].c.values == bk.d['XUSDT'].c.values).all()        # same timestamp -> same row


# ------------------------------------------------------------------ v3 re-review: lost answers, lagging position reports
class Amb(Exception): pass


def ambiguous_once(e, name, fill=True):
    """Next call of trade.<name> executes on the 'exchange' (if fill) but the answer is lost."""
    orig = getattr(e.trade, name)
    def f(*a, **k):
        setattr(e.trade, name, orig)
        if fill: orig(*a, **k)
        raise BC.AmbiguousOrder('response lost', 'c:zbtest')
    setattr(e.trade, name, f)


def test_lost_partial_tp_answer_does_not_fire_twice():
    e, _ = mk_engine(); sl = dict(SL, mgmt={'tp1_r': 1.0, 'tp1_frac': 0.5}); k = None
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity()); k = next(iter(e.state['lots'])); lot = e.state['lots'][k]
    q0 = lot['qty']; e.trade.mark['BTCUSDT'] = 120.0
    ambiguous_once(e, 'close'); e.manage(e.trade.marks())
    assert lot.get('pending')
    for _ in range(3): e.manage(e.trade.marks())
    assert abs(e.trade.pos[('BTCUSDT', 'LONG')] - q0 / 2) < 0.002               # exactly one half closed
    assert lot['tp1'] and not lot.get('pending') and abs(lot['qty'] - q0 / 2) < 0.002


def test_lost_pyramid_add_answer_is_not_repeated():
    e, _ = mk_engine(); sl = dict(SL, mgmt={'pyramid': {'n': 1, 'step_r': 1.0, 'frac': 0.5}}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity()); lot = next(iter(e.state['lots'].values())); q0 = lot['qty']
    e.trade.mark['BTCUSDT'] = 106.0
    ambiguous_once(e, 'open'); e.manage(e.trade.marks())
    for _ in range(3): e.manage(e.trade.marks())
    assert abs(e.trade.pos[('BTCUSDT', 'LONG')] - q0 * 1.5) < 0.002 and lot['adds'] == 1 and abs(lot['qty'] - q0 * 1.5) < 0.002
    assert not e.untracked


def test_lost_stop_answer_is_cleaned_up():
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]
    ambiguous_once(e, 'stop')
    assert e._replace_stop(lot, lot['stop'] + 1) is False and ['BTCUSDT', 'c:zbtest'] in e.state['orphans']
    e.manage(e.trade.marks())
    assert not lot['stop_dirty'] and e.state['orphans'] == []


def test_one_lagging_position_report_does_not_drop_a_lot():
    e, _ = mk_engine(); k = opened(e); e.state['lots'][k]['last_order_t'] = 0
    real = dict(e.trade.pos); e.trade.pos[('BTCUSDT', 'LONG')] = 0          # one stale read
    e.reconcile(500); assert k in e.state['lots']
    e.trade.pos.update(real); e.reconcile(500); assert k in e.state['lots'] and e.state['short_seen'] == {}


def test_lost_full_close_is_booked_at_its_price():
    e, _ = mk_engine(); k = opened(e); e.trade.mark['BTCUSDT'] = 110.0
    ambiguous_once(e, 'close')
    with pytest.raises(BC.AmbiguousOrder): e.close_lot(k, 'exit_signal', 110.0)
    e.manage(e.trade.marks())
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'exit_signal' and e.history[-1]['pnl'] > 0


def test_cairo_fallback_late_thursday_utc():
    assert E._egypt_offset(datetime(2026, 10, 29, 21, 30)) == timedelta(hours=2)   # already Friday 00:30 local -> winter


def test_config_keeps_values_it_could_not_read(app):
    with open(app.config_path(), 'w') as f: f.write('MODE=paper\nAPI_KEY=bad key with spaces\nAPI_SECRET="' + 'C' * 64 + '"\n')
    c = app.load_cfg(); assert c.get('API_SECRET') == 'C' * 64 and 'API_KEY' not in c
    app.write_cfg({'TELEGRAM_TOKEN': '123456789:' + 'A' * 35})
    txt = open(app.config_path()).read(); assert 'API_KEY=bad key with spaces' in txt


def test_zero_turns_management_off(app):
    assert app.clean_mgmt({'be_r': 0, 'tp_r': '0'}) == {'be_r': 0, 'tp_r': 0}


def test_dust_is_not_flagged_untracked():
    e, _ = mk_engine(); e.trade.pos[('ETHUSDT', 'LONG')] = 0.002          # 0.10 USDT of ETH: below Binance's minimum
    e.reconcile(500); e.reconcile(500); assert not e.untracked


def test_margin_type_error_does_not_block_entry():
    e, _ = mk_engine(); e.trade.fail.add('margin')
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())


def test_algo_cancel_string_200_is_success(monkeypatch):
    import binance_client as BC
    monkeypatch.setattr(BC.Futures, 'sync_time', lambda self: None)
    c = BC.Futures('k', 's')
    class R:
        status_code = 200; headers = {}; content = b'{"code":"200","msg":"success"}'; text = content.decode()
        def json(self): return {'code': '200', 'msg': 'success'}
    monkeypatch.setattr(c.s, 'request', lambda *a, **k: R())
    assert c.cancel('BTCUSDT', 'a:123') is True


# ------------------------------------------------------------------ adds (DCA safety orders / pyramid) pass the same gate as entries
PY = {'pyramid': {'n': 2, 'step_r': 1.0, 'frac': 0.5}}


def _pyr(e, **kw):
    sl = dict(SL, mgmt=PY, **kw); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    return sl, next(iter(e.state['lots'].values()))


def test_pyramid_add_respects_enforced_coin_cap():
    e, _ = mk_engine(); sl, lot = _pyr(e); q0 = lot['qty']
    cap = e.guard_eq or e.last_eq
    e.S['RISK_RULES'] = {'coin_cap': {'mode': 'enforce', 'x': (q0 * 106.0 * 1.2) / cap}}      # entry fits, entry + add does not
    e.trade.mark['BTCUSDT'] = 106.0; e.manage(e.trade.marks())
    assert lot['adds'] == 0 and abs(lot['qty'] - q0) < 1e-9 and 'coin_cap' in lot['add_blocked']
    assert any(m.get('kind') == 'add_blocked' for m in e.missed)
    e.S['RISK_RULES'] = {}; e.manage(e.trade.marks())
    assert lot['adds'] == 1 and 'add_blocked' not in lot


def test_pyramid_add_respects_enforced_open_risk_cap():
    e, _ = mk_engine(); sl, lot = _pyr(e)
    cap = e.guard_eq or e.last_eq
    e.S['RISK_RULES'] = {'open_risk_cap': {'mode': 'enforce', 'pct': e._lot_risk(lot) / cap * 100 + 0.01}}
    e.trade.mark['BTCUSDT'] = 106.0; e.manage(e.trade.marks())
    assert lot['adds'] == 0 and 'open_risk_cap' in lot['add_blocked']


def test_warn_mode_rules_never_block_adds():
    e, _ = mk_engine(); sl, lot = _pyr(e)
    e.S['RISK_RULES'] = {'coin_cap': {'mode': 'warn', 'x': 0.0001}, 'open_risk_cap': {'mode': 'warn', 'pct': 0.0001}}
    e.trade.mark['BTCUSDT'] = 106.0; e.manage(e.trade.marks())
    assert lot['adds'] == 1


def test_dca_safety_order_respects_enforced_coin_cap():
    e, _ = mk_engine()
    sl = dict(SL, key='dca_dip', mgmt={'dca': {'n': 3, 'step_atr': 1.0, 'scale': 1.5, 'tp_atr': 1.0, 'stop_atr': 2.0}}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    lot = next(iter(e.state['lots'].values())); q0 = lot['qty']; cap = e.guard_eq or e.last_eq
    e.S['RISK_RULES'] = {'coin_cap': {'mode': 'enforce', 'x': q0 * 100 * 1.1 / cap}}
    e.trade.mark['BTCUSDT'] = lot['levels'][0] - 0.01; e.manage(e.trade.marks())
    assert lot['dca'] == 0 and abs(lot['qty'] - q0) < 1e-9 and 'coin_cap' in lot['add_blocked']


def test_orphaned_lot_gets_no_adds_but_keeps_its_stop():
    e, _ = mk_engine(); sl, lot = _pyr(e); stop_id = lot['stop_id']
    e.S['SLEEVES'] = []                                     # slot removed by a profile change
    e.trade.mark['BTCUSDT'] = 106.0; e.manage(e.trade.marks())
    assert lot['adds'] == 0 and 'removed' in lot['add_blocked'] and lot['stop_id']


def test_reused_slot_id_with_other_strategy_gets_no_adds():
    e, _ = mk_engine(); sl, lot = _pyr(e)
    e.S['SLEEVES'] = [dict(sl, key='dca_dip', share=1.0)]   # same id 'T', different strategy and a bigger share
    e.trade.mark['BTCUSDT'] = 106.0; e.manage(e.trade.marks())
    assert lot['adds'] == 0 and 'replaced' in lot['add_blocked']


def test_add_cap_uses_share_recorded_at_entry():
    e, _ = mk_engine(); sl, lot = _pyr(e, share=0.5)
    assert lot['share'] == 0.5
    e.S['SLEEVES'] = [dict(sl, share=0.0005)]               # slot shrunk after the entry: the smaller share wins
    e.trade.mark['BTCUSDT'] = 106.0; e.manage(e.trade.marks())
    assert lot['adds'] == 0 and 'leverage cap' in lot['add_blocked']


def test_no_adds_while_paused_or_halted():
    for flag in ('pause', 'halt'):
        e, _ = mk_engine(); sl, lot = _pyr(e)
        if flag == 'pause': e.S['ENTRIES_PAUSED'] = True
        else: e.state['halted'] = True
        e.trade.mark['BTCUSDT'] = 106.0; e.manage(e.trade.marks())
        assert lot['adds'] == 0, flag


# ------------------------------------------------------------------ partial nested management settings
def test_partial_dca_override_keeps_defaults_engine_and_backtest():
    import strategies as S
    m = S.merge_mgmt('dca_dip', {'dca': {'n': 4}})
    assert m['dca'] == dict(n=4, step_atr=1.0, scale=1.5, tp_atr=1.0, stop_atr=2.0) and m['max_bars'] == 60
    assert S.merge_mgmt('ema_mom', {'pyramid': {'frac': 0.3}})['pyramid'] == dict(n=1, step_r=1.5, frac=0.3)
    e, _ = mk_engine()
    sl = dict(SL, key='dca_dip', mgmt={'dca': {'n': 4}}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    lot = next(iter(e.state['lots'].values()))
    assert len(lot['levels']) == 4 and lot['tp'] is not None
    e.trade.mark['BTCUSDT'] = lot['levels'][0] - 0.01; e.manage(e.trade.marks())       # no KeyError on tp_atr
    assert lot['dca'] == 1


def test_legacy_lot_with_half_filled_block_is_repaired_on_load(tmp_path):
    e, _ = mk_engine(str(tmp_path)); k = opened(e)
    e.state['lots'][k]['mgmt'] = {'stop_atr': 2.5, 'pyramid': {'n': 1}}; e.save_state()
    e2, _ = mk_engine(str(tmp_path))
    assert e2.state['lots'][k]['mgmt']['pyramid'] == dict(n=1, step_r=1.5, frac=0.5)


def test_backtest_partial_override_runs():
    import backtest as B
    rng = np.random.default_rng(0); n = 600
    t = pd.date_range('2025-01-01', periods=n, freq='4h'); c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    d = pd.DataFrame(dict(t=t, o=c, h=c * 1.01, l=c * 0.99, c=c, v=1000.0))
    bk = B.Book({'BTCUSDT': d, 'ETHUSDT': d.copy()})
    tr, cv = B.run(bk, [dict(key='dca_dip', share=1, risk=0.02, max_pos=2, sides='long', mgmt={'dca': {'n': 2}}, symbols=['BTCUSDT', 'ETHUSDT'])])
    assert len(cv) > 0


def test_exit_plan_explains_no_target_and_dca():
    e, _ = mk_engine(); k = opened(e); x = e.exit_plan(e.state['lots'][k])
    assert 'No fixed target' in x['title'] and any('exit signal' in s for s in x['steps'])
    sl = dict(SL, id='D', key='dca_dip'); e.S['SLEEVES'] = [SL, sl]
    assert e.open_lot(sl, 'ETHUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    lot = next(l for l in e.state['lots'].values() if l['sleeve'] == 'D'); x = e.exit_plan(lot)
    assert x['title'].startswith('Basket target') and x['next'].startswith('safety order 1')


def test_ai_prompt_uses_real_timeframe_and_claims_no_news():
    import ai_filter as A
    assert 'no news' in A.PROMPT and '{tf}' in A.PROMPT
    sig = dict(time='t', close=1.0, e20=1, e50=1, e200=1, atr=0.1, ret42=0.1, ret180=0.2, st_dir=1)
    msg = A.PROMPT.format(sleeve='X', symbol='BTCUSDT', tf='1h', side='SHORT', side_l='short', st='up', funding=0, candles=[], **sig)
    assert '1h candles' in msg and '4h' not in msg and 'SHORT signal' in msg
    assert A.review({}, 'BTCUSDT', 'X', sig, [], 0)[0] == 'take'          # no key -> rules decide


# ------------------------------------------------------------------ backtest must not use the future in trade MANAGEMENT
@pytest.mark.parametrize('key,mgmt', [('breakout_pyramid', {}), ('donchian_ens', {}), ('squeeze_tp', {}), ('dca_dip', {}),
                                      ('ema_mom', {'runner': {'be_r': 1, 'step_r': 1, 'gap_r': 1}}), ('ema_st', {'ttp': {'at_r': 1.5, 'dev_pct': 2}})])
def test_backtest_truncation_invariance(key, mgmt):
    """Cut the data at bar k: every trade that CLOSED before k must be identical with or without the later candles.
    Any difference means some decision used data from the future (this caught the same-candle ATR in the trailing stop)."""
    import backtest as B
    rng = np.random.default_rng(7); n = 1400
    t = pd.date_range('2024-01-01', periods=n, freq='4h')
    raw = {}
    for j, s in enumerate(['BTCUSDT', 'ETHUSDT', 'SOLUSDT']):
        r = rng.standard_t(4, n) * 0.012 + 0.0004 * np.sin(np.arange(n) / (60 + 15 * j))
        c = 100 * np.exp(np.cumsum(r)); o = np.r_[c[0], c[:-1]]
        w = np.abs(rng.normal(0, 0.008, n)) * c
        raw[s] = pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) + w, l=np.minimum(o, c) - w, c=c, v=1000.0))
    cfg = [dict(key=key, share=1, risk=0.02, max_pos=3, sides='both' if key != 'dca_dip' else 'long', mgmt=mgmt, symbols=list(raw))]
    k = 1100
    tr_full, _ = B.run(B.Book(raw), cfg)
    tr_cut, _ = B.run(B.Book({s: d.iloc[:k] for s, d in raw.items()}), cfg)
    done = lambda tr: tr[tr.i_out < k - 2].sort_values(['i_in', 'sym']).reset_index(drop=True)[['sym', 'i_in', 'i_out', 'pnl']].round(6)
    a, b = done(tr_full), done(tr_cut)
    assert len(a) >= 3, 'scenario produced too few trades to be meaningful'
    pd.testing.assert_frame_equal(a, b)


def test_exit_plan_manual_with_target():
    e, _ = mk_engine(); e.S['SLEEVES'] = [SL]
    e.open_lot(None, 'ETHUSDT', 'LONG', SG, None, e.equity(), risk=0.01, manual=True, stop_atr=2.0, tp_r=3)
    lot = next(l for l in e.state['lots'].values() if l.get('manual'))
    assert e.exit_plan(lot)['title'].startswith('Fixed target')


# ------------------------------------------------------------------ BTC circuit breaker vs adds (policy fixed per basket)
def _dca_lot(e, policy):
    e.S['RISK_RULES'] = {'btc_breaker': {'mode': 'enforce', 'pct': 5, 'hours': 4, 'dca': policy}}
    sl = dict(SL, key='dca_dip', mgmt={'dca': {'n': 3, 'step_atr': 1.0, 'scale': 1.5, 'tp_atr': 1.0, 'stop_atr': 2.0}}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    return next(iter(e.state['lots'].values()))


@pytest.mark.parametrize('policy,mult', [('pause', 0.0), ('half_size', 0.5), ('continue_plan', 1.0)])
def test_breaker_dca_policy(policy, mult):
    e, _ = mk_engine(); lot = _dca_lot(e, policy); q0 = lot['qty']
    assert lot['breaker_dca'] == policy
    e._breaker = lambda: None
    e.state['breaker_until'] = E.time.time() + 3600                        # breaker active
    e.S['RISK_RULES']['btc_breaker']['dca'] = 'continue_plan'               # a later setting change must NOT affect this basket
    e.trade.mark['BTCUSDT'] = lot['levels'][0] - 0.01; e.manage(e.trade.marks())
    added = lot['qty'] - q0
    if mult == 0: assert added == 0 and 'breaker' in lot['add_blocked']
    else: assert abs(added - q0 * lot['w'][0] * mult) < 0.002, (added, q0 * lot['w'][0] * mult)


def test_breaker_blocks_pyramid_adds_only_while_active():
    e, _ = mk_engine(); sl, lot = _pyr(e)
    e.S['RISK_RULES'] = {'btc_breaker': {'mode': 'enforce', 'pct': 5, 'hours': 4}}; e._breaker = lambda: None
    e.state['breaker_until'] = E.time.time() + 3600
    e.trade.mark['BTCUSDT'] = 106.0; e.manage(e.trade.marks())
    assert lot['adds'] == 0 and 'breaker' in lot['add_blocked'] and lot['stop_id']
    e.state['breaker_until'] = 0; e.manage(e.trade.marks())
    assert lot['adds'] == 1


def test_breaker_policy_validation():
    import app as A
    assert A.clean_risk_rules({'btc_breaker': {'dca': 'half_size'}})['btc_breaker']['dca'] == 'half_size'
    with pytest.raises(ValueError): A.clean_risk_rules({'btc_breaker': {'dca': 'yolo'}})
