"""ZackBot desktop app: trading engine + control panel in its own window.

Run:  python app.py            (or ZackBot.exe after building)
      python app.py --no-window  (server only; open http://localhost:8765 yourself)
"""
import csv, glob, json, logging, math, os, shutil, subprocess, sys, threading, time, traceback, uuid, socket
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import pandas as pd

BUNDLE = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))      # read-only app files
EXE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
PORT = 8765
VERSION = '2.2'


def data_dir():
    d = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ZackBot')
    os.makedirs(os.path.join(d, 'candles'), exist_ok=True)
    os.makedirs(os.path.join(d, 'backtests'), exist_ok=True)
    return d


DATA = data_dir()
LOG_F = os.path.join(DATA, 'bot.log')
_h = [logging.FileHandler(LOG_F, encoding='utf-8')]
if sys.stdout is not None: _h.append(logging.StreamHandler(sys.stdout))        # windowed exe has no console
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=_h)
log = logging.getLogger('zackbot')

import strategies as S          # noqa: E402
import backtest as BT           # noqa: E402
from engine import Engine, PRESETS, TOP40, CORE8, TF_SEC, save_json   # noqa: E402
from binance_client import Futures, MAINNET   # noqa: E402


# ------------------------------------------------------------------ config (keys)
def config_path():
    p = os.path.join(DATA, 'config.env')
    if not os.path.exists(p):
        for old in (os.path.join(EXE_DIR, 'config.env'), os.path.join(os.path.expanduser('~'), 'Documents', 'ZackBot', 'config.env')):
            if os.path.exists(old):
                shutil.copy(old, p); log.info(f'imported settings/keys from {old}'); break
    return p


def load_cfg():
    cfg = {}
    p = config_path()
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1); cfg[k.strip()] = v.strip()
    if cfg.get('MODE') == 'live' and cfg.get('LIVE_CONFIRM') != 'YES_REAL_MONEY':
        cfg['MODE'] = 'paper'
    cfg.setdefault('MODE', 'paper')
    return cfg


def write_cfg(updates):
    cfg = load_cfg(); cfg.update({k: v for k, v in updates.items() if v is not None})
    lines = ['# ZackBot keys and mode - edited from the app']
    for k in ('MODE', 'LIVE_CONFIRM', 'API_KEY', 'API_SECRET', 'ANTHROPIC_API_KEY', 'ANTHROPIC_MODEL'):
        lines.append(f'{k}={cfg.get(k, "")}')
    with open(config_path(), 'w', encoding='utf-8') as f: f.write('\n'.join(lines) + '\n')


# ------------------------------------------------------------------ candle cache for backtests
PUB = None


def get_candles(sym, tf, days):
    """Cached klines from Binance (public endpoint). Returns DataFrame t,o,h,l,c,v."""
    global PUB
    PUB = PUB or Futures('', '', MAINNET)
    f = os.path.join(DATA, 'candles', f'{sym}_{tf}.csv')
    step = TF_SEC[tf] * 1000
    need_from = int((time.time() - days * 86400 - 260 * TF_SEC[tf]) * 1000)
    df = pd.read_csv(f) if os.path.exists(f) else pd.DataFrame(columns=['t', 'o', 'h', 'l', 'c', 'v'])
    rows = []
    # backfill older history
    first = int(df.t.min()) if len(df) else int(time.time() * 1000)
    end = first - 1
    while end > need_from:
        k = PUB.klines(sym, tf, 1500, end_time=end)
        if not k: break
        rows += [r[:6] for r in k]
        end = int(k[0][0]) - 1
        if len(k) < 1500: break
    # forward fill to now
    last = int(df.t.max()) if len(df) else None
    if last:
        start = last + step
        while start < time.time() * 1000 - step:
            k = PUB.klines(sym, tf, 1500, start_time=start)
            if not k: break
            rows += [r[:6] for r in k]
            start = int(k[-1][0]) + step
            if len(k) < 1500: break
    if rows:
        new = pd.DataFrame(rows, columns=['t', 'o', 'h', 'l', 'c', 'v']).astype(float)
        df = pd.concat([df.astype(float), new]).drop_duplicates('t').sort_values('t')
        df = df[df.t < time.time() * 1000 - step]          # closed candles only
        df.to_csv(f, index=False)
    out = df.astype(float).copy()
    out['t'] = pd.to_datetime(out.t, unit='ms')
    return out.reset_index(drop=True)


# ------------------------------------------------------------------ backtest jobs
JOBS = {}


def run_backtest_job(job_id, req):
    """Runs each candle-size group (4h / 1h / 15m) on its own data with its share of capital, then adds the curves.
    Mixed profiles (e.g. Boost 4h + Active 1h) are therefore simulated as side-by-side sub-accounts."""
    job = JOBS[job_id]
    try:
        dtf = req.get('tf', '4h')
        days = int(req.get('days', 730))
        sleeves = [sl for sl in req['sleeves'] if sl.get('enabled', True)] or req['sleeves']
        start = float(req.get('start', 500))
        groups = {}
        for sl in sleeves: groups.setdefault(sl.get('tf') or dtf, []).append(sl)
        total_share = sum(float(sl['share']) for sl in sleeves) or 1
        trs, cvs, skipped, all_syms = [], [], [], set()
        for gi, (tf, gs) in enumerate(sorted(groups.items())):
            syms = sorted({s for sl in gs for s in (CORE8 if sl['symbols'] == 'core8' else req['universe'] if sl['symbols'] == 'all' else sl['symbols'])} | {'BTCUSDT'})
            raw = {}
            for i, s in enumerate(syms):
                job['status'] = f'downloading {tf} {s} ({i + 1}/{len(syms)})' + (f' - group {gi + 1}/{len(groups)}' if len(groups) > 1 else '')
                try:
                    d = get_candles(s, tf, days)
                    if len(d) * TF_SEC[tf] < (days * 86400) * 0.95 + 200 * TF_SEC[tf]: skipped.append(f'{s} {tf}'); continue
                    raw[s] = d[d.t >= d.t.iloc[-1] - pd.Timedelta(days=days) - pd.Timedelta(seconds=230 * TF_SEC[tf])]
                except Exception as e:
                    skipped.append(f'{s} {tf}'); log.warning(f'backtest data {s} {tf}: {e}')
            if 'BTCUSDT' not in raw: raise ValueError(f'no {tf} data for BTCUSDT')
            job['status'] = 'running' + (f' {tf} group' if len(groups) > 1 else '')
            book = BT.Book(raw)
            gshare = sum(float(sl['share']) for sl in gs)
            cfg = []
            for sl in gs:
                symbols = CORE8 if sl['symbols'] == 'core8' else req['universe'] if sl['symbols'] == 'all' else sl['symbols']
                cfg.append(dict(key=sl['key'], share=float(sl['share']) / gshare, risk=sl['risk'], max_pos=int(sl['max_pos']), id=sl.get('id'),
                                sides=sl.get('sides'), mgmt=sl.get('mgmt', {}), symbols=[s for s in symbols if s in raw],
                                hours=sl.get('hours'), vol_max_pct=sl.get('vol_max_pct'), kelly=sl.get('kelly')))
            gstart = start * gshare / total_share if len(groups) > 1 else start
            tr, cv = BT.run(book, cfg, start=gstart, max_lev=float(req.get('max_lev', 10)), daily_halt=float(req.get('daily_halt', 0.08)),
                            fund_per_bar=BT.FUND_PER_BAR * TF_SEC[tf] / 14400)
            if len(tr): tr = tr.assign(tf=tf)
            trs.append(tr); cvs.append(cv); all_syms |= set(raw)
        if len(cvs) == 1:
            cv = cvs[0]
        else:
            t0 = max(c.index[0] for c in cvs)
            idx = sorted(set().union(*[set(c.index) for c in cvs]))
            cv = sum(c.reindex(idx).ffill().bfill() for c in cvs)
            cv = cv[cv.index >= t0]
        tr = pd.concat([t for t in trs if len(t)], ignore_index=True) if any(len(t) for t in trs) else trs[0]
        mid = cv.index[0] + (cv.index[-1] - cv.index[0]) / 2
        st = BT.stats(tr, cv, start=start, split=str(mid))
        cvd = cv.resample('1D').last().dropna()
        by_sleeve = tr.groupby('sleeve').pnl.agg(['count', 'sum']).round(2).reset_index().to_dict('records') if len(tr) else []
        by_sym = tr.groupby('sym').pnl.sum().round(2).sort_values().to_dict() if len(tr) else {}
        ye = cv.resample('YE').last(); prev = float(cv.iloc[0]); years = {}
        for t, v in ye.items(): years[str(t.year)] = round((v / prev - 1) * 100, 1); prev = v
        st['per_week'] = round(len(tr) / max(1, (cv.index[-1] - cv.index[0]).days) * 7, 1)
        res = dict(id=job_id, name=req.get('name') or 'Backtest', created=datetime.now().isoformat(timespec='minutes'),
                   request=req, stats=st, skipped=skipped, years=years, period=[str(cv.index[0].date()), str(cv.index[-1].date())],
                   curve=[[str(t.date()), round(v, 2)] for t, v in cvd.items()], by_sleeve=by_sleeve, by_symbol=by_sym,
                   symbols=sorted(all_syms), tfs=sorted(groups))
        save_json(os.path.join(DATA, 'backtests', f'{job_id}.json'), res)
        job.update(status='done', result=res)
    except Exception as e:
        log.error('backtest failed: ' + traceback.format_exc())
        job.update(status='error', error=str(e))


def list_backtests():
    out = []
    for f in sorted(glob.glob(os.path.join(DATA, 'backtests', '*.json')), key=os.path.getmtime, reverse=True):
        try:
            r = json.load(open(f))
            out.append(dict(id=r['id'], name=r['name'], created=r['created'], stats=r['stats'], period=r['period'], years=r.get('years', {}),
                            curve=r['curve'][::7] if 'study' in r['id'] else None,
                            sleeves=[f"{s['key']}@{s['risk']:.1%}" for s in r['request']['sleeves']], tf='+'.join(r.get('tfs') or [r['request'].get('tf', '4h')])))
        except Exception:
            pass
    return out


def research():
    out = {}
    for name in ('results_single.csv', 'results_combos.csv', 'results_timeframes.csv', 'results_runner.csv'):
        p = os.path.join(BUNDLE, 'research', name)
        if os.path.exists(p):
            out[name] = pd.read_csv(p).fillna('').to_dict('records')
    p = os.path.join(BUNDLE, 'research', 'lead_traders.json')
    if os.path.exists(p): out['lead_traders'] = json.load(open(p))
    return out


# ------------------------------------------------------------------ coin logos
# Bundled SVGs (CC0 cryptocurrency-icons + MIT web3icons) for the top coins; anything else is
# fetched once from Binance's public asset list and cached in DATA/icons.
_ICON_FAIL, _ASSETS = {}, {'t': 0, 'map': {}}
_ICON_LOCK = threading.Lock()


def coin_icon(sym):
    base = ''.join(ch for ch in sym.upper().replace('.SVG', '').replace('.PNG', '') if ch.isalnum())
    if base.endswith('USDT'): base = base[:-4]
    for pre in ('1000000', '1000'):
        if base.startswith(pre) and len(base) > len(pre): base = base[len(pre):]
    if not base: return None
    f = os.path.join(BUNDLE, 'research', 'icons', base + '.svg')
    if os.path.exists(f): return open(f, 'rb').read(), 'image/svg+xml'
    cdir = os.path.join(DATA, 'icons'); os.makedirs(cdir, exist_ok=True)
    for ext, ct in (('.svg', 'image/svg+xml'), ('.png', 'image/png')):
        f = os.path.join(cdir, base + ext)
        if os.path.exists(f) and os.path.getsize(f) > 100: return open(f, 'rb').read(), ct
    if time.time() - _ICON_FAIL.get(base, 0) < 3600: return None
    with _ICON_LOCK:
        try:
            import requests
            if time.time() - _ASSETS['t'] > 3600 and not _ASSETS['map']:
                _ASSETS['t'] = time.time()
                try:
                    r = requests.get('https://www.binance.com/bapi/asset/v2/public/asset/asset/get-all-asset', timeout=8).json()
                    _ASSETS['map'] = {a.get('assetCode', '').upper(): (a.get('logoUrl') or a.get('fullLogoUrl')) for a in r.get('data', []) if a.get('logoUrl') or a.get('fullLogoUrl')}
                except Exception as e:
                    log.info(f'coin logo list unavailable: {e}')
            urls = [u for u in (_ASSETS['map'].get(base), f'https://bin.bnbstatic.com/static/assets/logos/{base}.png') if u]
            for u in urls:
                try:
                    r = requests.get(u, timeout=8)
                    if r.ok and len(r.content) > 100:
                        ct = r.headers.get('Content-Type', 'image/png').split(';')[0]
                        ext = '.svg' if 'svg' in ct else '.png'
                        open(os.path.join(cdir, base + ext), 'wb').write(r.content)
                        return r.content, ('image/svg+xml' if ext == '.svg' else 'image/png')
                except Exception:
                    pass
        except Exception as e:
            log.info(f'coin logo {base}: {e}')
        _ICON_FAIL[base] = time.time()
    return None


# ------------------------------------------------------------------ app state
class App:
    def __init__(self):
        self.engine = None
        self.lock = threading.RLock()
        self.research = research()
        self._want_preview = threading.Event()
        threading.Thread(target=self.preview_worker, daemon=True).start()
        self.start_engine()

    def start_engine(self):
        cfg = load_cfg()
        self.cfg = cfg
        eng = Engine(cfg, DATA)
        try:
            eng.connect()
        except Exception as e:
            eng.error = f'Could not connect to Binance: {e}'
            log.error(eng.error)
        self.engine = eng
        log.info(f"ZackBot {VERSION} | mode={'LIVE' if eng.live else 'PAPER'} | keys={'yes' if cfg.get('API_KEY') else 'no'} | data: {DATA}")
        self.preview()          # signals preview on start (no trading)

    def preview(self):
        """Ask the background worker to refresh signals (debounced, never blocks trading)."""
        self._want_preview.set()

    def preview_worker(self):
        while True:
            self._want_preview.wait(); time.sleep(1.5); self._want_preview.clear()
            e = self.engine
            try:
                if not e.rules: continue
                states, sigs_all = {}, {}
                for tf in sorted({sl['tf'] for sl in e.S['SLEEVES']}):
                    syms = sorted({s for sl in e.S['SLEEVES'] if sl['tf'] == tf for s in e.sleeve_symbols(sl)} |
                                  {s for s in e.S['UNIVERSE'] if s in e.rules})
                    sigs, frames = e.compute_signals(tf, syms)
                    sigs_all.update(sigs)
                    if tf == '4h' or not states:
                        for s, d in frames.items():
                            states[s] = dict(c=float(d.c.iloc[-1]), ema_up=bool(d.e20.iloc[-1] > d.e50.iloc[-1]), above200=bool(d.c.iloc[-1] > d.e200.iloc[-1]),
                                             st=int(d.st.iloc[-1]), mom=bool(d.ret42.iloc[-1] > 0 and d.ret180.iloc[-1] > 0),
                                             ret42=float(d.ret42.iloc[-1]), ret180=float(d.ret180.iloc[-1]), rsi=float(d.rsi14.iloc[-1]),
                                             atr_pct=float(d.atr.iloc[-1] / d.c.iloc[-1]), candle=str(d.t.iloc[-1]))
                e.signals = sigs_all; self.states = states
                try:   # 30-day correlation of 4h returns for traded coins (on + held), max 16
                    held = [l['symbol'] for l in e.state['lots'].values()]
                    cs = list(dict.fromkeys(held + [s for s in e.S['UNIVERSE'] if e.S['SYMBOLS_ON'].get(s)]))[:16]
                    rets = {s: e._kc[(s, '4h')][0].set_index('t').c.pct_change().iloc[-180:] for s in cs if (s, '4h') in e._kc}
                    if len(rets) >= 2:
                        cm = pd.DataFrame(rets).corr().round(2)
                        e.corr = dict(symbols=list(cm.columns), m=cm.fillna(0).values.tolist())
                except Exception as ex: log.warning(f'correlation: {ex}')
                e.signals_time = datetime.now(timezone.utc).isoformat(timespec='seconds')
            except Exception as ex:
                log.warning(f'signal preview failed: {ex}')

    states = {}

    def loop(self):
        last_bar = {}
        last_manage = last_eq = 0
        while True:
            try:
                e = self.engine
                if e and e.connected and e.cfg.get('API_KEY') and not e.error:
                    now = time.time()
                    tfs = sorted({sl['tf'] for sl in e.S['SLEEVES']} | {l.get('tf', '4h') for l in e.state['lots'].values()})
                    for tf in tfs:
                        bar = math.floor((now - 15) / TF_SEC[tf])
                        if tf not in last_bar: last_bar[tf] = bar
                        if bar != last_bar[tf]:
                            last_bar[tf] = bar
                            e.cycle(tf); self.preview()
                    if e.run_now.is_set():
                        e.run_now.clear()
                        for tf in tfs: e.cycle(tf, 'manual run'); self.preview()
                    if now - last_manage > 8:
                        last_manage = now
                        if e.state['lots']:
                            e.manage(e.trade.marks())
                    if now - last_eq > 300:
                        last_eq = now
                        with e.lock: e.record_equity(e.equity())
                    e.next_cycle = {tf: datetime.fromtimestamp((math.floor(now / TF_SEC[tf]) + 1) * TF_SEC[tf] + 15, timezone.utc).isoformat(timespec='seconds') for tf in tfs}
            except Exception as ex:
                log.error(f'main loop: {ex}')
                time.sleep(20)
            time.sleep(2)

    def snapshot(self):
        e = self.engine
        marks = {}
        try: marks = e.trade.marks() if e.connected else {}
        except Exception: pass
        lots = []
        for k, l in e.state['lots'].items():
            m = marks.get(l['symbol']); sd = 1 if l['side'] == 'LONG' else -1
            pnl = sd * (m - l['avg']) * l['qty'] if m else None
            lots.append(dict(key=k, **{x: l.get(x) for x in ('symbol', 'side', 'sleeve', 'qty', 'avg', 'e0', 'stop', 'opened', 'adds', 'dca', 'tp1', 'manual')},
                             mark=m, pnl=pnl, r=(pnl / l['risk_usd']) if (pnl is not None and l.get('risk_usd')) else None,
                             risk_to_stop=sd * ((m or l['avg']) - l['stop']) * l['qty'], notional=(m or l['avg']) * l['qty']))
        trades = []
        if os.path.exists(e.F['trades']):
            with open(e.F['trades']) as f: trades = list(csv.DictReader(f))
        realized = {}
        for t in trades:
            try: realized[t['sleeve']] = realized.get(t['sleeve'], 0) + float(t['pnl'] or 0)
            except ValueError: pass
        try:
            with open(LOG_F, encoding='utf-8') as f: logs = f.readlines()[-80:]
        except Exception: logs = []
        st = e.state
        # exposure
        eqv = e.last_eq or 0
        by_coin = {}
        for l in lots:
            c = by_coin.setdefault(l['symbol'], dict(long=0.0, short=0.0, risk=0.0, pnl=0.0))
            c['long' if l['side'] == 'LONG' else 'short'] += l['notional'] or 0
            c['risk'] += max(0.0, l['risk_to_stop'] or 0); c['pnl'] += l['pnl'] or 0
        by_sleeve = {}
        for l in lots:
            b = by_sleeve.setdefault(l['sleeve'], dict(notional=0.0, risk=0.0, n=0))
            b['notional'] += l['notional'] or 0; b['risk'] += max(0.0, l['risk_to_stop'] or 0); b['n'] += 1
        long_n = sum(c['long'] for c in by_coin.values()); short_n = sum(c['short'] for c in by_coin.values())
        acc = e.last_account or {}
        exposure = dict(long=long_n, short=short_n, gross=long_n + short_n, net=long_n - short_n,
                        gross_lev=(long_n + short_n) / eqv if eqv else 0, risk=sum(c['risk'] for c in by_coin.values()),
                        by_coin=by_coin, by_sleeve=by_sleeve, margin_used=acc.get('totalInitialMargin'), maint=acc.get('totalMaintMargin'),
                        margin_balance=acc.get('totalMarginBalance'), available=acc.get('availableBalance'), corr=e.corr)
        missed = []
        for m in e.missed[-300:][::-1]:
            mk = marks.get(m['symbol']); sd = 1 if m['side'] == 'LONG' else -1
            missed.append(dict(m, now=mk, move_pct=round(sd * (mk - m['price']) / m['price'] * 100, 2) if mk else None))
        return dict(version=VERSION, mode='LIVE' if e.live else 'PAPER', keys=bool(e.cfg.get('API_KEY')), ai_key=bool(e.cfg.get('ANTHROPIC_API_KEY')),
                    error=e.error, hedge=e.hedge, equity=e.last_eq, balance=e.last_balance, day_start=st.get('day_start_equity'),
                    peak=st.get('peak_equity'), halted=st.get('halted'), last_cycle=st.get('last_cycle'), next_cycle=getattr(e, 'next_cycle', None),
                    settings={k: (('•' * 8) if (k == 'TELEGRAM_TOKEN' and v) else v) for k, v in e.S.items()}, lots=lots, trades=trades[-150:][::-1], realized=realized,
                    signals=e.signals, states=self.states, signals_time=e.signals_time, equity_hist=e.equity_hist[-3000:],
                    logs=[x.rstrip() for x in logs][::-1], history=e.history[-1500:], missed=missed, exposure=exposure, capital=e.capital_info(), tradable=sorted(e.rules) if e.rules else [],
                    presets={k: dict(name=v['name'], note=v['note'], sleeves=v['sleeves']) for k, v in PRESETS.items()},
                    library={k: dict(name=v['name'], style=v['style'], sides=v['sides'], desc=v['desc'], mgmt=v['mgmt']) for k, v in S.STRATEGIES.items()},
                    core8=CORE8, top40=TOP40, jobs={k: {x: y for x, y in j.items() if x != 'result'} for k, j in JOBS.items()})


APP = None


# ------------------------------------------------------------------ HTTP
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body, ctype='application/json', cache='no-store'):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', cache); self.end_headers(); self.wfile.write(b)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str))

    def do_GET(self):
        p = self.path.split('?')[0]
        if p in ('/', '/index.html'):
            return self._send(200, open(os.path.join(BUNDLE, 'panel.html'), encoding='utf-8').read(), 'text/html; charset=utf-8')
        if p.startswith('/icon/'):
            r = coin_icon(p[6:])
            if r: return self._send(200, r[0], r[1], 'max-age=86400')
            return self._send(404, b'', 'image/svg+xml', 'max-age=600')
        if p == '/api/status': return self._json(APP.snapshot())
        if p == '/api/research': return self._json(APP.research)
        if p == '/api/backtests': return self._json(list_backtests())
        if p.startswith('/api/backtest/'):
            bid = p.rsplit('/', 1)[1]
            if bid in JOBS: return self._json({k: v for k, v in JOBS[bid].items()})
            f = os.path.join(DATA, 'backtests', f'{bid}.json')
            if os.path.exists(f): return self._json(dict(status='done', result=json.load(open(f))))
            return self._json(dict(status='missing'), 404)
        self._send(404, '{}')

    def do_POST(self):
        origin = self.headers.get('Origin', '')
        if origin and not origin.startswith((f'http://localhost:{PORT}', f'http://127.0.0.1:{PORT}')):
            return self._json(dict(ok=False, error='forbidden origin'), 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            self._json(dict(ok=True, msg=handle(self.path, body)))
        except (ValueError, KeyError) as e:
            log.warning(f'panel action rejected: {e}'); self._json(dict(ok=False, error=str(e)), 400)
        except Exception as e:
            log.error('panel action failed: ' + traceback.format_exc()); self._json(dict(ok=False, error=str(e)), 400)


LIMITS = dict(MAX_LEVERAGE=(1, 50), DAILY_LOSS_HALT=(0.01, 1.0), PEAK_DD_FLATTEN=(0.0, 0.95), CAPITAL_CAP=(0, 1e9))


def validate_sleeve(sl, i):
    if sl.get('key') not in S.STRATEGIES: raise ValueError(f'unknown strategy {sl.get("key")}')
    out = dict(id=str(sl.get('id') or f'S{i + 1}')[:12], key=sl['key'], name=S.STRATEGIES[sl['key']]['name'],
               enabled=bool(sl.get('enabled', True)), share=float(sl['share']), risk=float(sl['risk']), max_pos=int(sl['max_pos']),
               symbols=sl.get('symbols', 'all'), sides=sl.get('sides') or S.STRATEGIES[sl['key']]['sides'],
               mgmt=sl.get('mgmt') or {}, tf=sl.get('tf', '4h'))
    if sl.get('hours'): out['hours'] = [int(h) for h in sl['hours'] if 0 <= int(h) <= 23]
    if sl.get('vol_max_pct'): out['vol_max_pct'] = float(sl['vol_max_pct'])
    if sl.get('kelly'): out['kelly'] = dict(frac=float(sl['kelly'].get('frac', 0.5)), min=float(sl['kelly'].get('min', 0.25)), max=float(sl['kelly'].get('max', 2.0)))
    if not 0 < out['share'] <= 1: raise ValueError('capital share must be between 1% and 100%')
    if not 0.0005 <= out['risk'] <= 0.25: raise ValueError('risk per trade must be between 0.05% and 25%')
    if not 1 <= out['max_pos'] <= 20: raise ValueError('max positions must be 1-20')
    if out['sides'] not in ('long', 'short', 'both'): raise ValueError('sides must be long/short/both')
    if out['tf'] not in TF_SEC: raise ValueError('timeframe must be 15m, 1h or 4h')
    return out


def handle(path, b):
    e = APP.engine
    if path == '/api/settings':
        with e.lock:
            for k, v in b.items():
                if k in LIMITS:
                    lo, hi = LIMITS[k]; v = float(v)
                    if not lo <= v <= hi: raise ValueError(f'{k} must be between {lo} and {hi}')
                    e.S[k] = v
                elif k in ('ENTRIES_PAUSED', 'AI_FILTER', 'RUN_IN_BACKGROUND', 'TELEGRAM_ON'): e.S[k] = bool(v)
                elif k in ('TELEGRAM_TOKEN', 'TELEGRAM_CHAT'):
                    if str(v).strip() and set(str(v).strip()) != {'•'}: e.S[k] = str(v).strip()
                elif k == 'SYMBOLS_ON': e.S['SYMBOLS_ON'].update({s: bool(x) for s, x in v.items()})
                elif k == 'ADD_SYMBOL':
                    s = v.strip().upper(); s = s if s.endswith('USDT') else s + 'USDT'
                    if e.rules and s not in e.rules: raise ValueError(f'{s} is not a Binance USDT perpetual')
                    if s not in e.S['UNIVERSE']: e.S['UNIVERSE'].append(s); e.S['SYMBOLS_ON'][s] = True
                elif k == 'REMOVE_SYMBOL':
                    if v in e.S['UNIVERSE']: e.S['UNIVERSE'].remove(v); e.S['SYMBOLS_ON'].pop(v, None)
                elif k == 'UNIVERSE':
                    e.S['UNIVERSE'] = [s for s in v if not e.rules or s in e.rules]
                    e.S['SYMBOLS_ON'] = {s: e.S['SYMBOLS_ON'].get(s, True) for s in e.S['UNIVERSE']}
            e.save_settings()
        log.info('settings changed from panel: ' + ', '.join(b.keys()))
        APP.preview()
        return 'saved'
    if path == '/api/sleeves':
        sl = [validate_sleeve(x, i) for i, x in enumerate(b['sleeves'])]
        if len({x['id'] for x in sl}) != len(sl): raise ValueError('each strategy slot needs a unique name')
        if sum(x['share'] for x in sl if x['enabled']) > 1.0001: raise ValueError('capital shares of active strategies add up to more than 100%')
        with e.lock:
            e.S['SLEEVES'] = sl; e.S['PRESET'] = 'custom'; e.save_settings()
        log.info('strategies updated from panel: ' + ', '.join(f"{x['id']}={x['key']}@{x['risk']:.1%}" + ('' if x['enabled'] else '(off)') for x in sl))
        APP.preview()
        return 'strategies saved'
    if path == '/api/preset':
        e.apply_preset(b['name']); APP.preview()
        return f"preset {PRESETS[b['name']]['name']} applied"
    if path == '/api/keys':
        upd = {}
        for k in ('API_KEY', 'API_SECRET', 'ANTHROPIC_API_KEY'):
            if b.get(k): upd[k] = b[k].strip()
        if b.get('MODE') in ('paper', 'live'):
            if b['MODE'] == 'live' and b.get('CONFIRM') != 'YES_REAL_MONEY': raise ValueError('type YES_REAL_MONEY to switch to live')
            upd['MODE'] = b['MODE']; upd['LIVE_CONFIRM'] = 'YES_REAL_MONEY' if b['MODE'] == 'live' else ''
        if upd.get('MODE') and upd['MODE'] != APP.cfg.get('MODE') and e.state['lots']:
            raise ValueError('close all open positions before switching paper/live')
        write_cfg(upd)
        with APP.lock: APP.start_engine()
        return 'saved - engine restarted'
    if path == '/api/backtest':
        jid = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
        req = dict(b); req['universe'] = req.get('universe') or e.S['UNIVERSE']
        req['sleeves'] = [validate_sleeve(x, i) for i, x in enumerate(req['sleeves'])]
        JOBS[jid] = dict(id=jid, status='queued')
        threading.Thread(target=run_backtest_job, args=(jid, req), daemon=True).start()
        return jid
    if path == '/api/study':
        # backtest every ready-made profile over the same period, one after another (shares the candle cache)
        days = int(b.get('days', 1460)); start = float(b.get('start', 500))
        if any(j.get('study') and j['status'] not in ('done', 'error') for j in JOBS.values()):
            raise ValueError('a profile study is already running')
        keys = [k for k in PRESETS if k != 'original']
        sid = datetime.now().strftime('%Y%m%d-%H%M%S-') + 'study'
        JOBS[sid] = dict(id=sid, status='queued', study=True, done=0, total=len(keys), ids=[])

        def study():
            for n, k in enumerate(keys):
                jid = f'{sid}-{k}'
                JOBS[jid] = dict(id=jid, status='queued')
                JOBS[sid]['status'] = f'{PRESETS[k]["name"]} ({n + 1}/{len(keys)})'
                req = dict(name=f'{days // 365}y study · {PRESETS[k]["name"]}', sleeves=[validate_sleeve(dict(x), i) for i, x in enumerate(PRESETS[k]['sleeves'])],
                           days=days, tf='4h', start=start, max_lev=e.S['MAX_LEVERAGE'], daily_halt=e.S['DAILY_LOSS_HALT'], universe=list(TOP40))
                run_backtest_job(jid, req)
                JOBS[sid]['status'] = f'{PRESETS[k]["name"]}: ' + JOBS[jid]['status'] + f' ({n + 1}/{len(keys)})'
                JOBS[sid]['done'] = n + 1; JOBS[sid]['ids'].append(jid)
            JOBS[sid]['status'] = 'done'
        threading.Thread(target=study, daemon=True).start()
        return sid
    if path == '/api/backtest_delete':
        f = os.path.join(DATA, 'backtests', f"{b['id']}.json")
        if os.path.exists(f): os.remove(f)
        return 'deleted'
    if path == '/api/action':
        a = b.get('action')
        if a == 'run_cycle': e.run_now.set(); return 'cycle requested'
        if a == 'refresh_signals': APP.preview(); return 'refreshing signals'
        if a == 'flatten': e.flatten(); return 'all positions closed, entries paused'
        if a == 'close':
            with e.lock: e.close_lot(b['key'], 'closed_from_panel', e.trade.marks().get(e.state['lots'][b['key']]['symbol']))
            return 'closed'
        if a == 'telegram_test':
            if not (e.S.get('TELEGRAM_TOKEN') and e.S.get('TELEGRAM_CHAT')): raise ValueError('add the bot token and chat id first')
            import requests
            r = requests.post(f"https://api.telegram.org/bot{e.S['TELEGRAM_TOKEN']}/sendMessage", timeout=10,
                              data=dict(chat_id=e.S['TELEGRAM_CHAT'], text='ZackBot test message - notifications are working'))
            if not r.ok: raise ValueError(f'Telegram said: {r.text[:150]}')
            return 'test message sent'
        if a == 'take_signal':
            ok = e.take_signal(b['sleeve'], b['symbol'])
            return 'trade opened' if ok else 'skipped (size below Binance minimum or AI veto)'
        if a == 'capital': return e.capital_action(b['kind'], b.get('amount'), str(b.get('note', ''))[:80])
        if a == 'move_stop': e.move_stop(b['key'], float(b['stop'])); return 'stop moved'
        if a == 'manual_trade':
            ok = e.manual_trade(b['symbol'], b.get('side', 'LONG'), float(b['risk']) / 100, float(b['stop_atr']),
                                float(b['tp_r']) if b.get('tp_r') else None)
            return 'manual trade opened' if ok else 'skipped (size below Binance minimum)'
        if a == 'top_by_volume':
            t = Futures('', '', MAINNET).tickers_24h()
            ok = [x for x in t if x['symbol'].endswith('USDT') and (not e.rules or x['symbol'] in e.rules)]
            top = [x['symbol'] for x in sorted(ok, key=lambda x: -float(x['quoteVolume']))[:int(b.get('n', 40))]]
            return top
        if a == 'quit':
            log.info('quit from panel (exchange stops stay active)')
            threading.Timer(0.5, lambda: os._exit(0)).start(); return 'bye'
    raise ValueError('unknown request')


# ------------------------------------------------------------------ window
def find_browser():
    cands = [os.path.join(os.environ.get(v, ''), *p) for v in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA')
             for p in (('Microsoft', 'Edge', 'Application', 'msedge.exe'), ('Google', 'Chrome', 'Application', 'chrome.exe'))]
    return next((c for c in cands if os.path.exists(c)), None)


def open_window():
    b = find_browser()
    url = f'http://127.0.0.1:{PORT}'
    if not b:
        import webbrowser; webbrowser.open(url); return None
    prof = os.path.join(DATA, 'app-window')
    return subprocess.Popen([b, f'--app={url}', f'--user-data-dir={prof}', '--window-size=1560,980', '--no-first-run',
                             '--disable-extensions', '--disable-sync', '--disable-component-extensions-with-background-pages',
                             '--disable-features=Translate,msEdgeSync,EdgeCollections,msUndersideButton', '--no-default-browser-check',
                             '--disable-background-networking'])


def port_in_use():
    with socket.socket() as s:
        return s.connect_ex(('127.0.0.1', PORT)) == 0


def main():
    global APP
    if port_in_use():                       # already running -> just show it
        open_window(); return
    APP = App()
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=APP.loop, daemon=True).start()
    log.info(f'control panel at http://127.0.0.1:{PORT}')
    if '--no-window' in sys.argv:
        while True: time.sleep(3600)
    w = open_window()
    while True:
        time.sleep(2)
        if w is not None and w.poll() is not None:
            if APP.engine.S.get('RUN_IN_BACKGROUND', True):
                log.info('window closed - bot keeps running in the background (open ZackBot again to see it)')
                w = None
            else:
                log.info('window closed - quitting (exchange stops stay active)'); os._exit(0)


if __name__ == '__main__':
    main()
