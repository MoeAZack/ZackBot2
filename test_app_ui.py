"""ZackBot UI + API harness (T02 baseline).

Starts a PRIVATE copy of the app against a fake exchange and drives the control panel in headless Chromium.
It never touches the real bot: own temporary data folder, own free port, fake exchange, and it refuses to run
unless it can prove (session token + HMAC ping) that it is talking to the copy it started.

    python test_app_ui.py                 # full run (includes the backtest/study/lab jobs, ~5-10 min)
    ZB_UI_QUICK=1 python test_app_ui.py   # skip the long background jobs (~2 min)

Env: ZB_OUT (output folder, default dev_out), ZB_UI_PORT (force a port), ZB_UI_QUICK=1.
Writes <OUT>/ui_baseline/summary.json and screenshots per viewport. Exit code 0 = every check passed.
Needs: pip install playwright  +  python -m playwright install chromium
"""
import os, sys, json, time, tempfile, threading, shutil, socket, hmac, secrets, traceback
from datetime import datetime
from zoneinfo import ZoneInfo

for _s in (sys.stdout, sys.stderr):           # Windows consoles (cp1252) would crash on app text such as '·' or '−'
    try: _s.reconfigure(errors='replace')
    except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get('ZB_OUT') or os.path.join(HERE, 'dev_out')
BASE = os.path.join(OUT, 'ui_baseline')
QUICK = os.environ.get('ZB_UI_QUICK') == '1'
CAIRO = ZoneInfo('Africa/Cairo')
now_cairo = lambda: datetime.now(CAIRO).isoformat(timespec='seconds')
if os.path.isdir(BASE): shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(BASE, exist_ok=True)
sys.path.insert(0, HERE); os.chdir(HERE)

# ---------------------------------------------------------------- result recording
CHECKS, SHOTS, STARTED = [], [], now_cairo()
def check(name, ok, detail=''):
    CHECKS.append(dict(name=name, ok=bool(ok), detail=str(detail)[:400]))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{str(detail)[:160]}]' if detail and not ok else ''), flush=True)
    return bool(ok)

def finish(extra=None):
    fails = [c for c in CHECKS if not c['ok']]
    summary = dict(started=STARTED, finished=now_cairo(), timezone='Africa/Cairo', quick=QUICK, port=PORT,
                   passed=len(CHECKS) - len(fails), failed=len(fails), checks=CHECKS, screenshots=SHOTS, **(extra or {}))
    with open(os.path.join(BASE, 'summary.json'), 'w', encoding='utf-8') as f: json.dump(summary, f, indent=1, default=str)
    print(f"\nUI HARNESS: {'PASS' if not fails else 'FAIL'} - {len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed"
          f" - summary {os.path.join(BASE, 'summary.json')}")
    for c in fails: print('  failed:', c['name'], '-', c['detail'][:200])
    sys.stdout.flush()
    os._exit(0 if not fails else 1)            # daemon server threads: exit hard, with the right code

def free_port():
    with socket.socket() as s: s.bind(('127.0.0.1', 0)); return s.getsockname()[1]
PORT = int(os.environ.get('ZB_UI_PORT') or free_port())

# ---------------------------------------------------------------- isolated app on a fake exchange
TMP = tempfile.mkdtemp(prefix='zb_ui_'); os.environ['LOCALAPPDATA'] = TMP
os.makedirs(os.path.join(TMP, 'ZackBot'), exist_ok=True)
for f in ('history', 'missed'):              # seeds written by test_engine_sim.py (optional)
    for src in (os.path.join(OUT, f'seed_{f}.json'), os.path.join(HERE, 'dev_out', f'seed_{f}.json')):
        if os.path.exists(src): shutil.copy(src, os.path.join(TMP, 'ZackBot', f + '.json')); break
import pandas as pd, load_data
RAW = load_data.load()
MS = {s: (d.t.astype('datetime64[ns]').astype('int64') // 10**6).values for s, d in RAW.items()}
# 1h candles: the 4-year files when present (a 365-day study of the 1h profiles needs >= 95% coverage), else the 6-month ones
H1DIR = 'data_long/1h' if os.path.exists('data_long/1h/BTCUSDT_1h.csv') else 'data1h'
H1 = {s: pd.read_csv(f'{H1DIR}/{s}_1h.csv', parse_dates=['t']) for s in ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','LINKUSDT','AVAXUSDT']}
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
    def leverage_max(self, s): return 50
import binance_client; binance_client.Futures = Fake
import engine; engine.Futures = Fake
import app; app.Futures = Fake
app.load_cfg = lambda: dict(MODE='paper', API_KEY='k', API_SECRET='s')
# private port: the real ZackBot (8765) can keep running while this harness runs
app.PORT = PORT
app.HOSTS = (f'127.0.0.1:{PORT}', f'localhost:{PORT}')
app.ORIGINS = (f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}')
check('isolation: app data folder is the temporary one', os.path.realpath(app.DATA).startswith(os.path.realpath(TMP)), app.DATA)
check('isolation: harness port is not the real bot port', PORT != 8765, PORT)
sys.argv = ['app.py', '--no-window']
threading.Thread(target=app.main, daemon=True).start()

import requests as _rq
U = f'http://127.0.0.1:{PORT}'
SESSION = os.path.join(TMP, 'ZackBot', 'session.json')
TOK, t0 = None, time.time()
while time.time() - t0 < 90:                 # wait for our own instance: session file + valid HMAC ping
    try:
        tok = json.load(open(SESSION))['token']; nonce = secrets.token_hex(16)
        r = _rq.get(f'{U}/api/ping?nonce={nonce}', timeout=3).json()
        if r.get('app') == 'zackbot' and hmac.compare_digest(str(r.get('proof', '')), hmac.new(tok.encode(), nonce.encode(), 'sha256').hexdigest()):
            TOK = tok; break
    except Exception: pass
    time.sleep(0.5)
if not check('app started (own instance proved by HMAC ping)', TOK, f'no answer on {U} within 90 s'): finish()
print(f'app up on {U} in {time.time() - t0:.1f}s, data {app.DATA}')

# ---------------------------------------------------------------- auth boundary (no token / foreign origin)
check('auth: /api/status without token -> 401', _rq.get(U + '/api/status').status_code == 401)
check('auth: panel without token -> locked page 401', _rq.get(U + '/').status_code == 401)
check('auth: POST without token -> 401', _rq.post(U + '/api/action', json={'action': 'flatten'}).status_code == 401)
check('auth: POST from foreign origin -> 403', _rq.post(U + '/api/action', json={'action': 'flatten'},
      headers={'X-ZB-Token': TOK, 'Origin': 'http://evil.example'}).status_code == 403)

_S = _rq.Session(); _S.headers['X-ZB-Token'] = TOK
G = lambda p: _S.get(U + p).json()
P = lambda p, b: _S.post(U + p, json=b).json()
def wait_job(jid, limit=300, step=2):
    t = time.time()
    while time.time() - t < limit:
        j = G('/api/backtest/' + jid)
        if j['status'] in ('done', 'error', 'cancelled'): return j
        time.sleep(step)
    return dict(status='timeout')

# ---------------------------------------------------------------- API checks (existing harness, now asserted)
try:
    st = G('/api/status'); META = G('/api/meta'); st['presets'] = META['presets']
    check('api: status has equity, settings, health', all(k in st for k in ('equity', 'settings', 'health')) and st['health'].get('engine'), str(st.get('health')))
    check('api: preset applies', P('/api/preset', {'name': 'balanced'}).get('ok'))
    r = P('/api/sleeves', {'sleeves': [{'id': 'X', 'key': 'ema_mom', 'share': 0.7, 'risk': .02, 'max_pos': 4}, {'id': 'Y', 'key': 'dca_dip', 'share': 0.5, 'risk': .02, 'max_pos': 4}]})
    check('api: over-allocated slots rejected', r.get('ok') is False, r)
    r = P('/api/sleeves', {'sleeves': [{'id': 'MOM', 'key': 'ema_mom', 'share': 0.5, 'risk': .02, 'max_pos': 8, 'mgmt': {'pyramid': {'n': 1, 'step_r': 1.5, 'frac': .5}}, 'kelly': {'frac': .5}},
                                       {'id': 'BEAR', 'key': 'bear_breakdown', 'share': 0.25, 'risk': .02, 'max_pos': 4, 'sides': 'short', 'hours': [0, 4]},
                                       {'id': 'DCA', 'key': 'dca_dip', 'share': 0.25, 'risk': .02, 'max_pos': 4, 'vol_max_pct': .8}]})
    check('api: valid slots saved', r.get('ok'), r)
    check('api: unknown coin rejected', P('/api/settings', {'ADD_SYMBOL': 'nope'}).get('ok') is False)
    check('api: capital cap + leverage saved', P('/api/settings', {'CAPITAL_CAP': 500, 'MAX_LEVERAGE': 10}).get('ok'))
    check('api: manual long opens', P('/api/action', {'action': 'manual_trade', 'symbol': 'ETHUSDT', 'side': 'LONG', 'risk': 1, 'stop_atr': 2.5, 'tp_r': 3}).get('ok'))
    check('api: manual short opens', P('/api/action', {'action': 'manual_trade', 'symbol': 'SOLUSDT', 'side': 'SHORT', 'risk': 1, 'stop_atr': 2.5}).get('ok'))
    lots = G('/api/status')['lots']
    check('api: both manual lots have exchange stops', len(lots) == 2 and all(l.get('stop') for l in lots) and len(STOPS) >= 2, [(l['symbol'], l['side'], l.get('stop')) for l in lots])
    k = [l for l in lots if l['side'] == 'SHORT'][0]
    check('api: stop on the wrong side rejected', P('/api/action', {'action': 'move_stop', 'key': k['key'], 'stop': k['mark'] * 0.9}).get('ok') is False)
    check('api: valid stop move accepted', P('/api/action', {'action': 'move_stop', 'key': k['key'], 'stop': k['mark'] * 1.03}).get('ok'))
    check('api: take_signal without a signal rejected', P('/api/action', {'action': 'take_signal', 'sleeve': 'MOM', 'symbol': 'BTCUSDT'}).get('ok') is False)
    check('api: compounding on', P('/api/action', {'action': 'capital', 'kind': 'compound', 'amount': True}).get('ok'))
    check('api: withdraw + deposit recorded', P('/api/action', {'action': 'capital', 'kind': 'withdraw', 'amount': 50}).get('ok') and P('/api/action', {'action': 'capital', 'kind': 'deposit', 'amount': 20}).get('ok'))
    check('api: withdraw above capital rejected', P('/api/action', {'action': 'capital', 'kind': 'withdraw', 'amount': 99999}).get('ok') is False)
    c = G('/api/status')['capital']
    check('api: capital arithmetic 500-50+20=470', abs(c['capital'] - 470) < 1e-6, {k2: c[k2] for k2 in ('base', 'withdrawn', 'deposited', 'capital')})
    P('/api/action', {'action': 'capital', 'kind': 'reset', 'amount': 600}); P('/api/action', {'action': 'capital', 'kind': 'withdraw', 'amount': 25})
    c = G('/api/status')
    check('api: fresh start 600 then withdraw 25 -> 575', abs(c['capital']['capital'] - 575) < 1e-6 and len(c['capital']['cycles']) == 1, c['capital'].get('capital'))
    check('api: v3.1 settings saved', P('/api/settings', {'RISK_RULES': {'coin_cap': {'mode': 'enforce', 'x': 3}}, 'GOVERNOR': {'mode': 'suggest', 'rules': [{'if': 'dd_gte', 'value': 15, 'then': {'risk_mult': 0.5}, 'until': 'new_high'}]},
                                          'ENTRY_ORDER': 'maker', 'PUMP_GUARD': {'max_candle_atr': 3}, 'TELEGRAM_CONTROL': False}).get('ok'))
    check('api: governor risk multiplier out of range rejected', P('/api/settings', {'GOVERNOR': {'mode': 'auto', 'rules': [{'if': 'dd_gte', 'value': 10, 'then': {'risk_mult': 9}}]}}).get('ok') is False)
    check('api: grid preview', P('/api/grid_preview', {'symbol': 'BTCUSDT', 'cfg': {'levels': 12, 'share': 0.25}}).get('ok'))
    check('api: grid slots saved (disabled)', P('/api/grid_slots', {'slots': [{'id': 'G1', 'enabled': False, 'share': 0.1}]}).get('ok'))
    st3 = G('/api/status')
    check('api: status carries v3.1 blocks', all(k2 in st3 for k2 in ('risk_rules', 'governor', 'grids', 'telegram')))
    check('api: manual run_cycle accepted', P('/api/action', {'action': 'run_cycle'}).get('ok')); time.sleep(6)
    if not QUICK:
        j = wait_job(P('/api/lab', {'kind': 'monte_carlo', 'preset': 'calm', 'days': 365, 'n': 500})['msg'])
        check('job: lab Monte Carlo done', j['status'] == 'done', j.get('error') or j['status'])
        j = wait_job(P('/api/lab', {'kind': 'lookahead', 'days': 365, 'n_points': 4, 'keys': ['ema_mom']})['msg'])
        check('job: lab look-ahead check done and clean', j['status'] == 'done' and j.get('result', {}).get('ok'), j.get('error') or j.get('result', {}).get('ok'))
        j = wait_job(P('/api/backtest', {'name': 'ui test', 'sleeves': json.loads(json.dumps(st['presets']['calm']['sleeves'])), 'days': 365, 'tf': '4h', 'start': 500, 'max_lev': 10, 'universe': engine.CORE8})['msg'])
        check('job: backtest done with stats', j['status'] == 'done' and j.get('result', {}).get('stats'), j.get('error') or j['status'])
        js = wait_job(P('/api/study', {'days': 365})['msg'], limit=900, step=3)
        check('job: study done', js['status'] == 'done' and js.get('ids'), js.get('error') or js['status'])
        res = {i.split('-')[-1]: G('/api/backtest/' + i) for i in js.get('ids', [])}
        bad = {k2: (v.get('status'), v.get('error')) for k2, v in res.items() if not (v.get('status') == 'done' and (v.get('result') or {}).get('stats'))}
        check(f'job: study has a result for every profile ({len(res)})', res and not bad, bad)
        jm = wait_job(P('/api/backtest', {'name': 'mixed tf', 'sleeves': st['presets']['boost_active']['sleeves'], 'days': 180, 'tf': '4h', 'start': 500, 'max_lev': 10, 'universe': engine.CORE8})['msg'])
        check('job: mixed-timeframe backtest done', jm['status'] == 'done', jm.get('error') or jm['status'])
    r = _S.get(U + '/icon/BTC'); check('api: coin icon served as image', r.status_code == 200 and 'image' in (r.headers.get('Content-Type') or ''), r.status_code)
    sg = G('/api/signals'); check('api: signals endpoint', 'signals' in sg and 'states' in sg)
    check('api: research tables', isinstance(G('/api/research'), dict) and len(G('/api/research')) > 0)
    check('api: saved backtests list', isinstance(G('/api/backtests'), list))
except Exception as ex:
    check('api section ran without exceptions', False, traceback.format_exc()[-400:])

# ---------------------------------------------------------------- browser
TABS = ['dash', 'trades', 'risk', 'strat', 'coins', 'sig', 'bt', 'res', 'logs', 'set', 'help']
VIEWPORTS = [('desktop', 1560, 1000), ('tablet', 820, 1180), ('mobile', 390, 844)]
CONSOLE = {}                                   # context name -> [{type, text, url}]
NETFAIL = {}
EXPECTED_NOISE = ('/icon/',)                   # coin icons: the fake exchange has no network, a missing icon falls back to text

def wait_until(fn, timeout=8.0, step=0.15):
    t = time.time()
    while time.time() - t < timeout:
        try:
            if fn(): return True
        except Exception: pass
        time.sleep(step)
    return False

def new_page(browser, name, w, h):
    ctx = browser.new_context(viewport={'width': w, 'height': h}, device_scale_factor=1)
    pg = ctx.new_page(); CONSOLE[name] = []; NETFAIL[name] = []
    pg.on('console', lambda m: m.type in ('error', 'warning') and CONSOLE[name].append(dict(type=m.type, text=m.text[:300], url=(m.location or {}).get('url', ''))))
    pg.on('pageerror', lambda e: CONSOLE[name].append(dict(type='pageerror', text=str(e)[:300], url='')))
    pg.on('requestfailed', lambda r: NETFAIL[name].append(dict(url=r.url, why=r.failure)))
    pg.on('response', lambda r: r.status >= 400 and NETFAIL[name].append(dict(url=r.url, status=r.status)))
    pg.on('dialog', lambda d: d.accept())
    return ctx, pg

# The panel scrolls inside its own content area (body is overflow:hidden), so Playwright's full_page only captures one
# screen and document-level width checks see nothing. These helpers measure the tab's real scroll container instead.
SCROLLER_JS = """(t)=>{let e=document.getElementById('t_'+t);while(e&&e!==document.body){const s=getComputedStyle(e);
  if(/(auto|scroll)/.test(s.overflowY))return e;e=e.parentElement}return document.scrollingElement}"""
def overflow_x(pg, t):
    return pg.evaluate(f"(()=>{{const e=({SCROLLER_JS})('{t}');return Math.max(e.scrollWidth-e.clientWidth, document.documentElement.scrollWidth-window.innerWidth)}})()")

def shot(pg, rel, full=True, el=None, tab=None):
    p = os.path.join(BASE, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    pg.mouse.move(pg.viewport_size['width'] - 2, pg.viewport_size['height'] - 2)   # park the pointer: no hover tooltips in the evidence
    if el is not None: el.screenshot(path=p)
    elif full and tab:
        vs = pg.viewport_size
        extra = pg.evaluate(f"(()=>{{const e=({SCROLLER_JS})('{tab}');return e.scrollHeight-e.clientHeight}})()")
        if extra > 0: pg.set_viewport_size({'width': vs['width'], 'height': min(vs['height'] + extra, 12000)}); time.sleep(0.6)
        pg.screenshot(path=p)
        if extra > 0: pg.set_viewport_size(vs); time.sleep(0.4)
    else: pg.screenshot(path=p, full_page=full)
    SHOTS.append(rel.replace('\\', '/'))

def goto_tab(pg, t, narrow):
    if narrow and pg.locator('#hamb').is_visible():
        pg.click('#hamb'); pg.wait_for_selector('#side.open', timeout=3000)
    pg.click(f'#n_{t}')
    return wait_until(lambda: pg.evaluate(f"document.getElementById('t_{t}').classList.contains('on') && document.getElementById('n_{t}').getAttribute('aria-selected')==='true'"), 5)

def real_errors(name):
    return [c for c in CONSOLE[name] if c['type'] in ('error', 'pageerror') and not any(n in (c['url'] + c['text']) for n in EXPECTED_NOISE)]

def toast_text(pg):
    return pg.evaluate("(()=>{const t=document.getElementById('toast');return t&&t.style.display==='block'?t.textContent:''})()")

from playwright.sync_api import sync_playwright
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        # ---- every primary page at desktop / tablet / mobile
        for vp, w, h in VIEWPORTS:
            ctx, pg = new_page(b, vp, w, h)
            pg.goto(f'{U}/?t={TOK}')
            check(f'{vp}: panel loads and first status renders', wait_until(lambda: 'OFFLINE' not in pg.inner_text('#status') and pg.evaluate('typeof D==="object" && D!==null'), 20), pg.inner_text('#status'))
            narrow = w <= 640
            if narrow: check(f'{vp}: menu button shown, sidebar hidden until opened', pg.locator('#hamb').is_visible() and not pg.evaluate("document.getElementById('side').classList.contains('open')"))
            for t in TABS:
                ok = goto_tab(pg, t, narrow); time.sleep(0.6)
                body = pg.evaluate(f"document.getElementById('t_{t}').innerText.trim().length")
                over = overflow_x(pg, t)
                check(f'{vp}: tab {t} opens with content', ok and body > 20, f'active={ok} text={body}')
                check(f'{vp}: tab {t} has no sideways page scroll', over <= 1, f'{over}px wider than the screen')
                if narrow: check(f'{vp}: drawer closes after navigating to {t}', not pg.evaluate("document.getElementById('side').classList.contains('open')"))
                shot(pg, f'{vp}/{t}.png', tab=t)
            if vp == 'mobile':                 # prove the detector works: plant a too-wide block, expect it to be caught, remove it
                pg.evaluate("(()=>{const d=document.createElement('div');d.id='zb_probe';d.style.width='3000px';d.style.height='4px';document.getElementById('t_help').appendChild(d)})()")
                check('harness self-test: sideways-scroll detector catches a planted 3000px block', overflow_x(pg, 'help') > 1000)
                pg.evaluate("document.getElementById('zb_probe').remove()")
            pg.reload(); check(f'{vp}: last tab remembered after reload', wait_until(lambda: pg.evaluate("document.getElementById('t_help').classList.contains('on')"), 10))
            check(f'{vp}: no JS/console errors', not real_errors(vp), real_errors(vp)[:3])
            ctx.close()

        # ---- desktop deep flows + settings load/save round-trip
        ctx, pg = new_page(b, 'desktop_flows', 1560, 1000)
        pg.goto(f'{U}/?t={TOK}'); wait_until(lambda: pg.evaluate('typeof D==="object" && D!==null'), 20)
        S0 = G('/api/status')['settings']
        goto_tab(pg, 'set', False)
        wait_until(lambda: pg.inner_text('#ver').strip(), 5); ver = pg.inner_text('#ver')
        check('settings: version and build id shown', app.VERSION in ver and (app.BUILD_ID == 'dev' or str(app.BUILD_ID) in ver), ver[:120])
        goto_tab(pg, 'risk', False); time.sleep(0.5)
        shown = float(pg.input_value('#DAILY_LOSS_HALT') or 'nan')
        check('settings load: daily loss halt field equals saved value', abs(shown - S0['DAILY_LOSS_HALT'] * 100) < 0.05, f'field {shown} vs saved {S0["DAILY_LOSS_HALT"] * 100}')
        new = shown + 1 if shown < 50 else shown - 1
        pg.fill('#DAILY_LOSS_HALT', str(new)); pg.dispatch_event('#DAILY_LOSS_HALT', 'input')
        check('settings: unsaved-changes bar appears', wait_until(lambda: pg.locator('#usRisk').is_visible(), 3))
        pg.click('#t_risk button:has-text("Save limits")')
        check('settings save: server stored the new value', wait_until(lambda: abs(G('/api/status')['settings']['DAILY_LOSS_HALT'] * 100 - new) < 0.05, 6))
        check('settings save: "Saved" confirmation shown', wait_until(lambda: 'Saved' in toast_text(pg), 4), toast_text(pg))
        pg.reload(); wait_until(lambda: pg.evaluate('typeof D==="object" && D!==null'), 20); goto_tab(pg, 'risk', False); time.sleep(0.5)
        check('settings round-trip: value survives a reload', abs(float(pg.input_value('#DAILY_LOSS_HALT')) - new) < 0.05, pg.input_value('#DAILY_LOSS_HALT'))
        pg.fill('#DAILY_LOSS_HALT', str(shown)); pg.dispatch_event('#DAILY_LOSS_HALT', 'input'); pg.click('#t_risk button:has-text("Save limits")')
        check('settings: original value restored', wait_until(lambda: abs(G('/api/status')['settings']['DAILY_LOSS_HALT'] - S0['DAILY_LOSS_HALT']) < 1e-9, 6))
        goto_tab(pg, 'dash', False); time.sleep(0.4)
        bg0 = G('/api/status')['settings'].get('RUN_IN_BACKGROUND')
        pg.click('label:has(#sw_bg)')
        check('settings: switch saves immediately', wait_until(lambda: G('/api/status')['settings'].get('RUN_IN_BACKGROUND') == (not bg0), 6))
        pg.click('label:has(#sw_bg)'); wait_until(lambda: G('/api/status')['settings'].get('RUN_IN_BACKGROUND') == bg0, 6)
        # existing harness flows (screenshots kept as the visual baseline)
        flows = [('trades: open view', lambda: (goto_tab(pg, 'trades', False), pg.click('#trView [data-v="open"]')), 'flows/trades_open.png'),
                 ('trades: missed view', lambda: pg.click('#trView [data-v="missed"]'), 'flows/trades_missed.png'),
                 ('dash: explanations off', lambda: (goto_tab(pg, 'dash', False), pg.click('label:has(#helpsw)')), 'flows/dash_nohelp.png'),
                 ('dash: explanations back on', lambda: pg.click('label:has(#helpsw)'), None),
                 ('strat: combine profile', lambda: (goto_tab(pg, 'strat', False), pg.click('.preset:has-text("Active (1h") button:has-text("Combine")')), 'flows/strat_combine.png'),
                 ('trades: calendar day', lambda: (goto_tab(pg, 'trades', False), pg.click('#trView [data-v="closed"]'), time.sleep(.5), pg.click('.cal .d.has >> nth=0')), 'flows/trades_day.png'),
                 ('strat: advanced slot settings', lambda: (goto_tab(pg, 'strat', False), pg.click('button:has-text("Advanced") >> nth=0')), 'flows/strat_advanced.png')]
        if not QUICK: flows.insert(3, ('backtest: open saved result', lambda: (goto_tab(pg, 'bt', False), pg.click('#bt_list button:has-text("View") >> nth=0')), 'flows/bt_detail.png'))
        for name, fn, sp in flows:
            try: fn(); time.sleep(1); sp and shot(pg, sp, tab=pg.evaluate('CUR')); check('flow: ' + name, True)
            except Exception as ex: check('flow: ' + name, False, str(ex).splitlines()[0])
        pg.set_viewport_size({'width': 1560, 'height': 2600})
        for cid, t in (('posCards', 'dash'), ('resLong', 'res'), ('rrCard', 'risk'), ('gridCard', 'strat'), ('labCard', 'bt'), ('tgcCard', 'set'), ('res31', 'res'), ('roCard', 'bt')):
            try:
                goto_tab(pg, t, False); time.sleep(1); el = pg.locator('#' + cid)
                if cid == 'posCards': pg.evaluate("document.querySelectorAll('#posCards details').forEach(d=>d.open=true)"); time.sleep(.3)
                if not el.count(): check(f'card {cid} present', False, 'missing'); continue
                tg = pg.locator(f'#{cid} .cardh')
                if tg.count() and tg.first.get_attribute('aria-expanded') == 'false': tg.first.click(); time.sleep(.6)
                el.scroll_into_view_if_needed(); shot(pg, f'cards/{cid}.png', el=el); check(f'card {cid} present and visible', el.is_visible())
            except Exception as ex: check(f'card {cid} present and visible', False, str(ex).splitlines()[0])
        check('desktop flows: no JS/console errors', not real_errors('desktop_flows'), real_errors('desktop_flows')[:3])
        # stray 5xx responses during normal use are bugs (4xx from deliberate bad input in flows are not)
        bad5 = [n for n in NETFAIL['desktop_flows'] if n.get('status', 0) >= 500]
        check('desktop flows: no server errors (5xx)', not bad5, bad5[:3])
        ctx.close()

        # ---- API failures are visible and handled (injected in the browser; the server is not changed)
        ctx, pg = new_page(b, 'api_failures', 1560, 1000)
        pg.goto(f'{U}/?t={TOK}'); wait_until(lambda: pg.evaluate('typeof D==="object" && D!==null'), 20)
        status = lambda: pg.inner_text('#status')
        pg.route('**/api/status', lambda r: r.fulfill(status=500, body='Internal error', content_type='text/plain'))
        pg.evaluate('load()')
        check('api failure: HTTP 500 shows APP OFFLINE', wait_until(lambda: 'OFFLINE' in status(), 6), status())
        shot(pg, 'failures/status_500.png', full=False)
        check('api failure: panel stays usable while offline', goto_tab(pg, 'risk', False) and goto_tab(pg, 'dash', False))
        pg.unroute('**/api/status'); pg.evaluate('load()')
        check('api failure: recovers when the API answers again', wait_until(lambda: 'OFFLINE' not in status(), 8), status())
        pg.route('**/api/status', lambda r: r.abort('connectionrefused'))
        pg.evaluate('load()')
        check('api failure: connection refused shows APP OFFLINE', wait_until(lambda: 'OFFLINE' in status(), 6), status())
        pg.unroute('**/api/status'); pg.evaluate('load()'); wait_until(lambda: 'OFFLINE' not in status(), 8)
        bg0 = G('/api/status')['settings'].get('RUN_IN_BACKGROUND')
        pg.route('**/api/settings', lambda r: r.fulfill(status=200, body=json.dumps({'ok': False, 'error': 'harness: simulated rejection'}), content_type='application/json'))
        pg.click('label:has(#sw_bg)')
        check('api failure: rejected save shows the error message', wait_until(lambda: 'simulated rejection' in toast_text(pg), 5), toast_text(pg))
        shot(pg, 'failures/save_rejected.png', full=False)
        check('api failure: switch returns to the saved state', wait_until(lambda: pg.is_checked('#sw_bg') == bool(bg0), 8) and G('/api/status')['settings'].get('RUN_IN_BACKGROUND') == bg0)
        pg.unroute('**/api/settings')
        pg.route('**/api/settings', lambda r: r.fulfill(status=502, body='<html>Bad gateway</html>', content_type='text/html'))
        pg.click('label:has(#sw_bg)')
        check('api failure: non-JSON 502 shows "bad response"', wait_until(lambda: 'bad response' in toast_text(pg), 5), toast_text(pg))
        pg.unroute('**/api/settings'); wait_until(lambda: pg.is_checked('#sw_bg') == bool(bg0), 8)
        pg.route('**/api/status', lambda r: r.fulfill(status=401, body=json.dumps({'ok': False, 'error': 'not authorised'}), content_type='application/json'))
        pg.evaluate('load()')
        check('api failure: 401 shows the session-expired banner', wait_until(lambda: pg.locator('#expired').is_visible() and 'EXPIRED' in status(), 6), status())
        check('api failure: polling stops after session expiry', pg.evaluate('POLL===null'))
        shot(pg, 'failures/session_expired.png', full=False)
        unexpected = [c for c in CONSOLE['api_failures'] if c['type'] == 'pageerror']
        check('api failures: handled without uncaught JS exceptions', not unexpected, unexpected[:3])
        ctx.close(); b.close()
except Exception:
    check('browser section ran without exceptions', False, traceback.format_exc()[-400:])

try:
    r = P('/api/action', {'action': 'flatten'})
    check('cleanup: flatten closes every fake position', not {k2: v for k2, v in POS.items() if v > 1e-9}, POS)
except Exception as ex:
    check('cleanup: flatten', False, ex)
finish(dict(app_version=app.VERSION, build=str(app.BUILD_ID), viewports=VIEWPORTS, console=CONSOLE,
            network_failures={k2: v[:50] for k2, v in NETFAIL.items()}))
