import sys, os, tempfile, threading, time, json
SP='/tmp/claude-0/-home-claude/d6ad53d0-10de-5d7c-a74c-77ea7be8c649/scratchpad'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(); os.environ['LOCALAPPDATA'] = tmp
import shutil; os.makedirs(os.path.join(tmp,'ZackBot'), exist_ok=True)
for f in ('history','missed'):
    if os.path.exists('/tmp/claude-0/-home-claude/d6ad53d0-10de-5d7c-a74c-77ea7be8c649/scratchpad/seed_'+f+'.json'): shutil.copy('/tmp/claude-0/-home-claude/d6ad53d0-10de-5d7c-a74c-77ea7be8c649/scratchpad/seed_'+f+'.json', os.path.join(tmp,'ZackBot',f+'.json'))
import pandas as pd, load_data
RAW = load_data.load()
MS = {s: (d.t.astype('datetime64[ns]').astype('int64') // 10**6).values for s, d in RAW.items()}
H1 = {s: pd.read_csv(f'data1h/{s}_1h.csv', parse_dates=['t']) for s in ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','LINKUSDT','AVAXUSDT']}
MS1 = {s: (d.t.astype('datetime64[ns]').astype('int64') // 10**6).values for s, d in H1.items()}
POS = {}; STOPS = {}; N = [0]
class Fake:
    def __init__(self, *a, **k): pass
    def sync_time(self): pass
    def exchange_info(self):
        return {'symbols': [{'symbol': s, 'contractType': 'PERPETUAL', 'status': 'TRADING', 'filters': [
            {'filterType': 'MARKET_LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001'}, {'filterType': 'PRICE_FILTER', 'tickSize': '0.0001'},
            {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]} for s in RAW]}
    def klines(self, s, tf, limit=1500, end_time=None, start_time=None):
        d, ms = (H1[s], MS1[s]) if tf == '1h' and s in H1 else (RAW[s], MS[s])
        m = (ms <= end_time) if end_time else (ms >= start_time) if start_time else ms > 0
        idx = [i for i in range(len(ms)) if m[i]]
        idx = idx[-limit:] if not start_time else idx[:limit]
        return [[int(ms[i]), d.o[i], d.h[i], d.l[i], d.c[i], d.v[i], int(ms[i]) + 14400000 - 1] for i in idx]
    def marks(self): return {s: float(d.c.iloc[-1]) for s, d in RAW.items()}
    def premium(self, s): return {'lastFundingRate': '0.0001'}
    def tickers_24h(self): return [{'symbol': s, 'quoteVolume': str(float(d.v.iloc[-1] * d.c.iloc[-1]))} for s, d in RAW.items()]
    def account(self): return {'totalMarginBalance': '5000'}
    def positions(self): return {k: v for k, v in POS.items() if v > 0}
    def hedge_mode(self): return True
    def set_hedge_mode(self, on=True): pass
    def set_leverage(self, *a): pass
    def set_margin_type(self, *a): pass
    def open(self, s, ps, q): POS[(s, ps)] = POS.get((s, ps), 0) + float(q); return {'avgPrice': str(self.marks()[s])}
    def close(self, s, ps, q): POS[(s, ps)] = POS.get((s, ps), 0) - float(q); return {'avgPrice': str(self.marks()[s])}
    def stop(self, s, ps, q, p): N[0] += 1; STOPS[f'o:{N[0]}'] = s; return f'o:{N[0]}'
    def cancel(self, s, t): STOPS.pop(t, None)
    def cancel_all(self, s): pass
    def open_stop_tags(self, s): return {k for k, v in STOPS.items() if v == s}
import binance_client; binance_client.Futures = Fake
import engine; engine.Futures = Fake
import app; app.Futures = Fake
app.load_cfg = lambda: dict(MODE='paper', API_KEY='k', API_SECRET='s')
sys.argv = ['app.py', '--no-window']
threading.Thread(target=app.main, daemon=True).start()
time.sleep(8)
import requests
U = 'http://127.0.0.1:8765'
st = requests.get(U + '/api/status').json(); print('status ok: eq', st['equity'], 'sleeves', [s['id'] for s in st['settings']['SLEEVES']], 'signals', len(st['signals']), 'states', len(st['states']))
P = lambda p, b: requests.post(U + p, json=b).json()
print('preset', P('/api/preset', {'name': 'balanced'}))
print('bad sleeve', P('/api/sleeves', {'sleeves': [{'id': 'X', 'key': 'ema_mom', 'share': 0.7, 'risk': .02, 'max_pos': 4}, {'id': 'Y', 'key': 'dca_dip', 'share': 0.5, 'risk': .02, 'max_pos': 4}]}))
print('good sleeves', P('/api/sleeves', {'sleeves': [{'id': 'MOM', 'key': 'ema_mom', 'share': 0.5, 'risk': .02, 'max_pos': 8, 'mgmt': {'pyramid': {'n': 1, 'step_r': 1.5, 'frac': .5}}, 'kelly': {'frac': .5}},
                                                     {'id': 'BEAR', 'key': 'bear_breakdown', 'share': 0.25, 'risk': .02, 'max_pos': 4, 'sides': 'short', 'hours': [0, 4]},
                                                     {'id': 'DCA', 'key': 'dca_dip', 'share': 0.25, 'risk': .02, 'max_pos': 4, 'vol_max_pct': .8}]}))
print('add coin', P('/api/settings', {'ADD_SYMBOL': 'nope'}), P('/api/settings', {'CAPITAL_CAP': 500, 'MAX_LEVERAGE': 10}))
print('manual long', P('/api/action', {'action': 'manual_trade', 'symbol': 'ETHUSDT', 'side': 'LONG', 'risk': 1, 'stop_atr': 2.5, 'tp_r': 3}))
print('manual short', P('/api/action', {'action': 'manual_trade', 'symbol': 'SOLUSDT', 'side': 'SHORT', 'risk': 1, 'stop_atr': 2.5}))
st = requests.get(U + '/api/status').json(); lots = st['lots']; print('lots', [(l['symbol'], l['side'], l['qty'], round(l['stop'], 3)) for l in lots])
k = [l for l in lots if l['side'] == 'SHORT'][0]
print('move stop wrong side', P('/api/action', {'action': 'move_stop', 'key': k['key'], 'stop': k['mark'] * 0.9}))
print('move stop ok', P('/api/action', {'action': 'move_stop', 'key': k['key'], 'stop': k['mark'] * 1.03}))
print('take signal (expected error ok)', P('/api/action', {'action': 'take_signal', 'sleeve': 'MOM', 'symbol': 'BTCUSDT'}))
print('compound on', P('/api/action', {'action': 'capital', 'kind': 'compound', 'amount': True}))
print('withdraw', P('/api/action', {'action': 'capital', 'kind': 'withdraw', 'amount': 50}), 'deposit', P('/api/action', {'action': 'capital', 'kind': 'deposit', 'amount': 20}))
print('withdraw too much', P('/api/action', {'action': 'capital', 'kind': 'withdraw', 'amount': 99999}))
c = requests.get(U + '/api/status').json()['capital']; print('capital', {k: c[k] for k in ('mode', 'base', 'realized', 'withdrawn', 'deposited', 'capital', 'growth')})
print('reset', P('/api/action', {'action': 'capital', 'kind': 'reset', 'amount': 600}), 'withdraw', P('/api/action', {'action': 'capital', 'kind': 'withdraw', 'amount': 25}))
c = requests.get(U + '/api/status').json(); print('after reset', {k: c['capital'][k] for k in ('base', 'capital', 'withdrawn')}, 'cycles', len(c['capital']['cycles']), 'bot equity', c['equity'])
print('run cycle', P('/api/action', {'action': 'run_cycle'})); time.sleep(6)
print('backtest', jid := P('/api/backtest', {'name': 'ui test', 'sleeves': json.loads(json.dumps(st['presets']['calm']['sleeves'])), 'days': 365, 'tf': '4h', 'start': 500, 'max_lev': 10, 'universe': engine.CORE8}))
for _ in range(60):
    j = requests.get(U + '/api/backtest/' + jid['msg']).json()
    if j['status'] in ('done', 'error'): break
    time.sleep(2)
sid = P('/api/study', {'days': 365})['msg']
for _ in range(200):
    js = requests.get(U + '/api/backtest/' + sid).json()
    if js['status'] in ('done', 'error'): break
    time.sleep(3)
print('study', js['status'], js.get('done'), [(i.split('-')[-1], requests.get(U + '/api/backtest/' + i).json().get('result', {}).get('stats', {}).get('end')) for i in js.get('ids', [])])
mix = P('/api/backtest', {'name': 'mixed tf', 'sleeves': st['presets']['boost_active']['sleeves'], 'days': 180, 'tf': '4h', 'start': 500, 'max_lev': 10, 'universe': engine.CORE8})
for _ in range(60):
    jm = requests.get(U + '/api/backtest/' + mix['msg']).json()
    if jm['status'] in ('done', 'error'): break
    time.sleep(2)
print('mixed backtest', jm['status'], jm.get('error'), jm.get('result', {}).get('tfs'), jm.get('result', {}).get('stats'))
print('icon BTC', requests.get(U + '/icon/BTC').status_code, requests.get(U + '/icon/BTC').headers.get('Content-Type'), 'icon TAO (no net) ', requests.get(U + '/icon/TAO').status_code)
print('backtest result', j['status'], j.get('error'), j.get('result', {}).get('stats'))
st2=requests.get(U + '/api/status').json(); print('signals later', len(st2['signals']), 'states', len(st2['states']))
print('research', list(requests.get(U + '/api/research').json().keys()), 'saved', len(requests.get(U + '/api/backtests').json()))
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    b = pw.chromium.launch(); pg = b.new_page(viewport={'width': 1560, 'height': 1000}); errs = []
    pg.on('pageerror', lambda e: errs.append(str(e))); pg.on('dialog', lambda d: d.accept())
    pg.goto(U); time.sleep(3)
    for t in ['dash', 'trades', 'risk', 'strat', 'coins', 'sig', 'bt', 'res', 'logs', 'set', 'help']:
        pg.click(f'#n_{t}'); time.sleep(1.2); pg.screenshot(path=f'/tmp/claude-0/-home-claude/d6ad53d0-10de-5d7c-a74c-77ea7be8c649/scratchpad/ui_{t}.png', full_page=True)
    pg.click('#n_trades'); time.sleep(1)
    for v in ('open','missed'):
        pg.click(f'#trView [data-v="{v}"]'); time.sleep(1); pg.screenshot(path=SP+'/ui_trades_'+v+'.png', full_page=True)
    pg.click('#n_dash'); time.sleep(.5); pg.click('label:has(#helpsw)'); time.sleep(.5); pg.screenshot(path=SP+'/ui_dash_nohelp.png', full_page=True)
    pg.click('#n_res'); time.sleep(2); pg.screenshot(path=SP+'/ui_res_runner.png')
    pg.click('#n_bt'); time.sleep(1); pg.click('#bt_list button:has-text("View")'); time.sleep(1.5); pg.screenshot(path='/tmp/claude-0/-home-claude/d6ad53d0-10de-5d7c-a74c-77ea7be8c649/scratchpad/ui_bt_detail.png', full_page=True)
    pg.click('#n_strat'); time.sleep(1); pg.click('.preset:has-text("Active (1h") button:has-text("Combine")'); time.sleep(1); pg.screenshot(path=SP+'/ui_combine.png', full_page=True)
    pg.click('#n_trades'); time.sleep(1); pg.click('#trView [data-v="closed"]'); time.sleep(.5); pg.click('.cal .d.has >> nth=0'); time.sleep(1); pg.screenshot(path=SP+'/ui_trades_day.png', full_page=True)
    pg.click('#n_strat'); time.sleep(1); pg.click('button:has-text("Advanced") >> nth=0'); time.sleep(.5); pg.screenshot(path='/tmp/claude-0/-home-claude/d6ad53d0-10de-5d7c-a74c-77ea7be8c649/scratchpad/ui_strat_adv.png', full_page=True)
    print('JS errors:', errs); b.close()
print('flatten', P('/api/action', {'action': 'flatten'}), 'positions after', {k: v for k, v in POS.items() if v > 1e-9})
