"""ZackBot desktop app: trading engine + control panel in its own window.

Run:  python app.py            (or ZackBot.exe after building)
      python app.py --no-window  (server only; open http://localhost:8765 yourself)
"""
import base64, csv, glob, gzip, hmac, json, logging, logging.handlers, math, os, queue, random, re, secrets, shutil, subprocess, sys, threading, time, traceback, uuid, socket
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs

import pandas as pd

BUNDLE = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))      # read-only app files
EXE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
PORT = 8765
VERSION = '3.2'
try: from build_info import BUILD_ID          # written by build_app.bat for each build (verified after the build)
except ImportError: BUILD_ID = 'dev'


def data_dir():
    d = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ZackBot')
    os.makedirs(os.path.join(d, 'candles'), exist_ok=True)
    os.makedirs(os.path.join(d, 'backtests'), exist_ok=True)
    return d


DATA = data_dir()
LOG_F = os.path.join(DATA, 'bot.log')
SECRETS = set()                     # values that must never appear in a log line


class Scrub(logging.Filter):
    """Removes API keys / Telegram tokens from every log record (they would otherwise reach bot.log and the Logs tab)."""
    TG = re.compile(r'bot\d{5,}:[A-Za-z0-9_-]{20,}')

    def filter(self, rec):
        msg = rec.getMessage()
        clean = self.TG.sub('bot***', msg)
        for x in list(SECRETS):
            if x and len(x) >= 8: clean = clean.replace(x, '***')
        if clean != msg: rec.msg, rec.args = clean, ()
        return True


_h = [logging.handlers.RotatingFileHandler(LOG_F, maxBytes=5_000_000, backupCount=3, encoding='utf-8')]
if sys.stdout is not None: _h.append(logging.StreamHandler(sys.stdout))        # windowed exe has no console
for h_ in _h: h_.addFilter(Scrub())
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=_h)
log = logging.getLogger('zackbot')

import strategies as S          # noqa: E402
import backtest as BT           # noqa: E402
from engine import Engine, PRESETS, TOP40, CORE8, TF_SEC, MANUAL_MAX_RISK, save_json, next_reset_utc, UNVERIFIED_BT01   # noqa: E402
from binance_client import Futures, MAINNET   # noqa: E402
import grid as GRID             # noqa: E402
import lab as LAB               # noqa: E402
import feasibility as F         # noqa: E402
import exchange_rules as XRULES  # noqa: E402
from engine import RISK_RULE_DEFAULTS, GOV_MULT_MAX, EXCHANGE_DOWN   # noqa: E402
from telegram_ctl import TelegramControl, clean_setting as tg_clean_setting   # noqa: E402


# ------------------------------------------------------------------ config (keys) - validated, atomically written, encrypted on Windows
CFG_KEYS = ('MODE', 'LIVE_CONFIRM', 'API_KEY', 'API_SECRET', 'ANTHROPIC_API_KEY', 'ANTHROPIC_MODEL', 'TELEGRAM_TOKEN')
CFG_SECRET = ('API_KEY', 'API_SECRET', 'ANTHROPIC_API_KEY', 'TELEGRAM_TOKEN')
CFG_RULES = dict(MODE=r'paper|live', LIVE_CONFIRM=r'(YES_REAL_MONEY)?', API_KEY=r'[A-Za-z0-9]{8,128}', API_SECRET=r'[A-Za-z0-9]{8,128}',
                 ANTHROPIC_API_KEY=r'[A-Za-z0-9_\-]{8,200}', ANTHROPIC_MODEL=r'[A-Za-z0-9.\-_]{0,80}', TELEGRAM_TOKEN=r'\d{5,15}:[A-Za-z0-9_\-]{20,80}')


def cfg_value_ok(k, v):
    return k in CFG_RULES and isinstance(v, str) and (v == '' or re.fullmatch(CFG_RULES[k], v) is not None)


def _dpapi(data, encrypt):
    """Windows Data Protection API: secrets can only be decrypted by this Windows user on this PC."""
    import ctypes
    from ctypes import wintypes as w

    class BLOB(ctypes.Structure):
        _fields_ = [('cbData', w.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))); out = BLOB()
    c32 = ctypes.windll.crypt32
    if encrypt: ok = c32.CryptProtectData(ctypes.byref(inp), ctypes.c_wchar_p('ZackBot'), None, None, None, 0x1, ctypes.byref(out))
    else: ok = c32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out))
    if not ok: raise OSError('DPAPI call failed')
    try: return ctypes.string_at(out.pbData, out.cbData)
    finally: ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(v):
    if not v or os.name != 'nt': return v
    try:
        enc = 'dpapi:' + base64.b64encode(_dpapi(v.encode(), True)).decode()
        if unprotect(enc) == v: return enc           # only store encrypted if it decrypts back exactly
    except Exception as e:
        log.warning(f'key encryption unavailable ({type(e).__name__}) - stored as plain text')
    return v


def unprotect(v):
    if not v.startswith('dpapi:'): return v
    return _dpapi(base64.b64decode(v[6:]), False).decode()


def config_path():
    p = os.path.join(DATA, 'config.env')
    if not os.path.exists(p):
        for old in (os.path.join(EXE_DIR, 'config.env'), os.path.join(os.path.expanduser('~'), 'Documents', 'ZackBot', 'config.env')):
            if os.path.exists(old):
                shutil.copy(old, p); log.info(f'imported settings/keys from {old} - you can delete that old copy'); break
    return p


_RAW = {}                       # config lines that failed validation/decryption: kept untouched when the file is rewritten


def load_cfg():
    cfg, plain = {}, False
    _RAW.clear()
    p = config_path()
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            k, v = line.split('=', 1); k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'': v = v[1:-1].strip()     # hand-edited "quoted" values
            if k in cfg or k in _RAW: log.warning(f'config.env: duplicate {k} ignored'); continue      # first value wins, injected repeats are ignored
            if k in CFG_SECRET and v.startswith('dpapi:'):
                try: v = unprotect(v)
                except Exception as e:
                    _RAW[k] = v; log.error(f'could not decrypt {k} ({type(e).__name__}) - please enter it again in Settings'); continue
            elif k in CFG_SECRET and v: plain = True
            if not cfg_value_ok(k, v):
                if k in CFG_RULES and '\n' not in v: _RAW[k] = v
                log.warning(f'config.env: invalid value for {k} ignored'); continue
            cfg[k] = v
    for k in CFG_SECRET:
        if cfg.get(k): SECRETS.add(cfg[k])
    if cfg.get('MODE') == 'live' and cfg.get('LIVE_CONFIRM') != 'YES_REAL_MONEY':
        cfg['MODE'] = 'paper'
    cfg.setdefault('MODE', 'paper')
    cfg['_plain'] = plain and os.name == 'nt'
    return cfg


def write_cfg(updates):
    for k, v in updates.items():
        if v is not None and not cfg_value_ok(k, v): raise ValueError(f'{k}: invalid format (letters, digits, dash and underscore only)')
    cfg = load_cfg(); cfg.pop('_plain', None); cfg.update({k: v for k, v in updates.items() if v is not None})
    raw = dict(_RAW)
    lines = ['# ZackBot keys and mode - edited from the app (secrets are encrypted for this Windows user)']
    for k in CFG_KEYS:
        if k not in updates and k not in cfg and k in raw:
            lines.append(f'{k}={raw[k]}'); continue                 # never wipe a value we could not read
        v = cfg.get(k, '')
        lines.append(f'{k}={protect(v) if k in CFG_SECRET else v}')
    tmp = config_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: f.write('\n'.join(lines) + '\n')
    os.replace(tmp, config_path())
    for k in CFG_SECRET:
        if cfg.get(k): SECRETS.add(cfg[k])
    stale = os.path.join(DATA, 'src', 'config.env')                  # v2 installer copied the plaintext keys here
    if os.path.exists(stale):
        try: os.remove(stale); log.info('removed an old plaintext copy of the keys from the app folder')
        except OSError: pass


# ------------------------------------------------------------------ candle cache for backtests
PUB = None


_CANDLE_LOCKS, _CL = {}, threading.Lock()


def get_candles(sym, tf, days):
    """Cached klines from Binance (public endpoint). Returns DataFrame t,o,h,l,c,v. One writer per cache file at a time."""
    if not re.fullmatch(r'[A-Z0-9]{2,20}USDT', sym) or tf not in TF_SEC: raise ValueError('bad symbol/timeframe')
    with _CL: lk = _CANDLE_LOCKS.setdefault((sym, tf), threading.Lock())
    with lk:
        return _get_candles(sym, tf, days)


def _get_candles(sym, tf, days):
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
        tmp = f + '.tmp'; df.to_csv(tmp, index=False); os.replace(tmp, f)     # atomic: a crash never leaves a half-written cache
    out = df.astype(float).copy()
    out['t'] = pd.to_datetime(out.t, unit='ms')
    return out.reset_index(drop=True)


# ------------------------------------------------------------------ backtest jobs (one worker, bounded queue)
JOBS = {}
JOBQ = queue.Queue(maxsize=8)
BT_ID = re.compile(r'^[0-9]{8}-[0-9]{6}-[a-z0-9_\-]{2,40}$')


def job_worker():
    while True:
        fn, args = JOBQ.get()
        try: fn(*args)
        except Exception: log.error('job failed: ' + traceback.format_exc())
        finally:
            done = [k for k, j in JOBS.items() if j.get('status') in ('done', 'error')]
            for k in done[:-30]: JOBS.pop(k, None)            # keep memory bounded


def enqueue(fn, *args):
    try: JOBQ.put_nowait((fn, args))
    except queue.Full: raise ValueError('too many backtests waiting - try again when the current ones finish')


def exchange_rules_now(e=None):
    """BT02: the exchange rules backtests and the preflight use -> (snapshot, state, detail).
    The connected engine's own exchangeInfo rules (this environment, read at connect) win; otherwise the snapshot file
    data/exchange_rules_<testnet|mainnet>.json. Testnet and mainnet are never mixed."""
    if e is None and APP is not None: e = APP.engine
    env = 'mainnet' if e is not None and e.live else 'testnet'
    meta = getattr(e, 'rules_meta', None) if e is not None else None
    if e is not None and e.rules and meta and meta.get('environment') == env:
        snap = dict(schema=F.SCHEMA, version=0, environment=env, source=meta['source'], fetched_at=meta['fetched_at'],
                    verified=True, provenance='engine', note='', symbols=e.rules)
    else:
        snap = XRULES.load(env)
    st, detail = F.snapshot_state(snap, time.time(), env)
    return snap, st, detail


def feas_total(feas):
    """BT02: per-timeframe feasibility counts -> totals (executable %, skips by reason, per symbol, per slot)."""
    out = dict(feas, executed=0, rule_blocked=0, skipped={}, add_skipped={}, by_symbol={}, by_slot={}, unknown_symbols=[])
    for g in feas['groups'].values():
        out['executed'] += g.get('executed', 0); out['rule_blocked'] += g.get('rule_blocked', 0)
        for k in ('skipped', 'add_skipped'):
            for c, n in (g.get(k) or {}).items(): out[k][c] = out[k].get(c, 0) + n
        for k in ('by_symbol', 'by_slot'):
            for c, v in (g.get(k) or {}).items():
                x = out[k].setdefault(c, dict(attempts=0, skipped=0)); x['attempts'] += v['attempts']; x['skipped'] += v['skipped']
        out['unknown_symbols'] = sorted(set(out['unknown_symbols']) | set(g.get('unknown_symbols') or []))
    n = out['executed'] + sum(out['skipped'].values())
    out['executable_pct'] = round(out['executed'] / n * 100, 1) if n else None
    return out


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
        trs, cvs, skipped, all_syms, gaps = [], [], [], set(), {}
        xsnap, xstate, xdetail = exchange_rules_now() if req.get('exchange_rules', 'on') != 'off' else (None, 'off', 'exchange rules off')
        # BT02 review P1: only a valid, verified, fresh snapshot of THIS environment may change the canonical result
        # (entries, equity, PF, DD). Anything else runs the legacy floor and says so; it is never a promotable number.
        xapply = xsnap if xstate == 'ok' else None
        feas = dict(rules_state=xstate, rules_detail=xdetail, environment=(xsnap or {}).get('environment'), groups={},
                    rules_applied=xapply is not None, promotable=xapply is not None,
                    rules_version=(xapply or {}).get('version'), rules_fetched_at=(xapply or {}).get('fetched_at'))
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
            gaps.update({f'{k} {tf}': v for k, v in book.gaps.items()})
            gshare = sum(float(sl['share']) for sl in gs)
            cfg = []
            for sl in gs:
                symbols = CORE8 if sl['symbols'] == 'core8' else req['universe'] if sl['symbols'] == 'all' else sl['symbols']
                cfg.append(dict(key=sl['key'], share=float(sl['share']) / gshare, risk=sl['risk'], max_pos=int(sl['max_pos']), id=sl.get('id'),
                                sides=sl.get('sides'), mgmt=sl.get('mgmt', {}), symbols=[s for s in symbols if s in raw],
                                hours=sl.get('hours'), vol_max_pct=sl.get('vol_max_pct'), kelly=sl.get('kelly'),
                                **{k: sl[k] for k in ('when', 'trail_entry', 'pump_guard') if sl.get(k) is not None}))
            gstart = start * gshare / total_share if len(groups) > 1 else start
            tr, cv = BT.run(book, cfg, start=gstart, max_lev=float(req.get('max_lev', 10)), daily_halt=float(req.get('daily_halt', 0.08)),
                            fund_per_bar=BT.FUND_PER_BAR * TF_SEC[tf] / 14400, exchange_rules=xapply, **(req.get('run_options') or {}))
            fz = dict(cv.attrs.get('feasibility') or {}); fz['skips'] = (fz.get('skips') or [])[:100]
            feas['groups'][tf] = fz
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
        cut = cv.index[0] + (cv.index[-1] - cv.index[0]) * 0.7          # last 30% of the period, reported on its own
        a_, b_ = cv[cv.index < cut], cv[cv.index >= cut]
        oos = dict(split=str(cut.date()), ret_first=round((a_.iloc[-1] / a_.iloc[0] - 1) * 100, 1) if len(a_) > 1 else None,
                   ret_last=round((b_.iloc[-1] / b_.iloc[0] - 1) * 100, 1) if len(b_) > 1 else None,
                   dd_last=round((b_ / b_.cummax() - 1).min() * 100, 1) if len(b_) > 1 else None)
        dd_curve = [[str(t.date()), round((v / m - 1) * 100, 2)] for t, v, m in zip(cvd.index, cvd.values, cvd.cummax().values)]
        by_sleeve = tr.groupby('sleeve').pnl.agg(['count', 'sum']).round(2).reset_index().to_dict('records') if len(tr) else []
        by_sym = tr.groupby('sym').pnl.sum().round(2).sort_values().to_dict() if len(tr) else {}
        ye = cv.resample('YE').last(); prev = float(cv.iloc[0]); years = {}
        for t, v in ye.items(): years[str(t.year)] = round((v / prev - 1) * 100, 1); prev = v
        st['per_week'] = round(len(tr) / max(1, (cv.index[-1] - cv.index[0]).days) * 7, 1)
        res = dict(id=job_id, name=req.get('name') or 'Backtest', created=datetime.now().isoformat(timespec='minutes'),
                   request=req, stats=st, skipped=skipped, years=years, engine=BT.VERSION, oos=oos, dd_curve=dd_curve,
                   gaps=gaps, period=[str(cv.index[0].date()), str(cv.index[-1].date())],
                   curve=[[str(t.date()), round(v, 2)] for t, v in cvd.items()], by_sleeve=by_sleeve, by_symbol=by_sym,
                   symbols=sorted(all_syms), tfs=sorted(groups), feasibility=feas_total(feas))
        fz = res['feasibility']
        fz['execution_realistic'] = bool(fz.get('rules_applied')) and not fz.get('unknown_symbols')
        fz['promotable'] = fz['execution_realistic']
        save_json(os.path.join(DATA, 'backtests', f'{job_id}.json'), res)
        job.update(status='done', result=res)
    except Exception as e:
        log.error('backtest failed: ' + traceback.format_exc())
        job.update(status='error', error=str(e))


_PF_FILES = {}


def preflight_market(e, keys, now=None):
    """{(symbol, tf): dict(px, atr, atr_median, t, basis, stale)} for the preflight. px / atr: the LATEST closed candle's
    close and ATR - what the engine sizes the next signal with (signal atr = the last closed candle's d.atr). atr_median:
    the median ATR/price of the last 180 closed candles x last close, a separate planning estimate only (BT02 review P2).
    Source: the engine's candle cache (closed candles only), else the candle files shipped with the app - their last row
    may have been a still-forming candle when the file was written, so it is dropped, and any candle that has not closed
    by `now` is cut (the engine's rule). stale = the latest closed candle is more than 2 candles older than it should be:
    the preflight then says 'unknown' (never a green pass on old prices) and keeps its estimate."""
    out, asof = {}, {}
    now = time.time() if now is None else float(now)
    kc = getattr(e, '_kc', None) or {}
    for sym, tf in sorted(keys):
        df = kc.get((sym, tf), (None,))[0]
        src = 'engine'
        if df is None or not len(df):
            src = 'shipped file'
            f = os.path.join(BUNDLE, {'4h': 'data', '1h': 'data1h'}.get(tf, 'data'), f'{sym}_{tf}.csv')
            if not os.path.exists(f): continue
            try:
                ck = (f, os.path.getmtime(f))
                if ck not in _PF_FILES:
                    raw = pd.read_csv(f, parse_dates=['t'], encoding='utf-8').tail(401).reset_index(drop=True)
                    _PF_FILES[ck] = S.indicators(raw.iloc[:-1].reset_index(drop=True))     # last row: completion unknown
                df = _PF_FILES[ck]
            except Exception: continue
        if 'atr' not in df or not len(df): continue
        tf_s = TF_SEC.get(tf, 14400)
        opened = pd.to_datetime(df.t).map(lambda x: x.timestamp())                  # unit-safe (ns / us / s)
        closed = (opened + tf_s) <= now                                             # the engine keeps only closed candles
        if not closed.all(): df = df[closed.values].reset_index(drop=True)
        if len(df) < 30: continue
        tail = df.tail(180)
        ratio = float((tail.atr / tail.c).median()); px = float(df.c.iloc[-1]); atr = float(df.atr.iloc[-1])
        if not (ratio > 0 and px > 0 and atr > 0): continue
        close_t = pd.Timestamp(df.t.iloc[-1]).timestamp() + tf_s
        out[(sym, tf)] = dict(px=px, atr=atr, atr_median=ratio * px, t=str(df.t.iloc[-1])[:16], basis='latest closed candle',
                              src=src, stale=bool(now - close_t > 2 * tf_s))
        asof[f'{sym} {tf}'] = f'{str(df.t.iloc[-1])[:16]} ({src})'
    return out, asof


def lab_book(q):
    """Candles for a lab job: the slots' coins + BTC, period + warm-up, from the shared candle cache."""
    tf, days = q['tf'], int(q['days'])
    syms = set(['BTCUSDT'])
    for sl in q.get('sleeves') or []:
        sy = sl.get('symbols', 'all')
        syms |= set(CORE8 if sy == 'core8' else (q.get('universe') or TOP40) if sy == 'all' else sy)
    if not q.get('sleeves'): syms |= set(CORE8)
    raw = {}
    for s_ in sorted(syms):
        try:
            d = get_candles(s_, tf, days)
            if len(d) * TF_SEC[tf] >= (days * 86400) * 0.95 + 200 * TF_SEC[tf]:
                raw[s_] = d[d.t >= d.t.iloc[-1] - pd.Timedelta(days=days) - pd.Timedelta(seconds=230 * TF_SEC[tf])]
        except Exception as ex:
            log.warning(f'lab data {s_}: {ex}')
    if 'BTCUSDT' not in raw: raise ValueError('no BTC data for that period')
    return BT.Book(raw)


def run_lab(jid, kind, req):
    job = JOBS[jid]
    def prog(f, text=''): job.update(progress=round(float(f), 3), status=f'running - {text}' if text else 'running')
    try:
        if req.get('preset') in PRESETS and not req.get('sleeves'):
            req = dict(req, sleeves=[validate_sleeve(dict(x), i) for i, x in enumerate(PRESETS[req['preset']]['sleeves'])])
        res = LAB.run_lab_job(kind, req, lab_book, prog, lambda: job.get('cancel'))
        res.update(id=jid, kind=kind, name=f"Lab · {kind.replace('_', ' ')}", created=datetime.now().isoformat(timespec='minutes'),
                   # BT02 review decision 2: lab runs use the legacy floor, never exchange rules -> exploratory, never promotable
                   execution=dict(exchange_rules='not applied (legacy 5 USDT floor)', execution_realistic=False, promotable=False))
        save_json(os.path.join(DATA, 'backtests', f'{jid}.json'), dict(res, lab=True))
        job.update(status='done', result=res, progress=1.0)
    except LAB.Cancelled:
        job.update(status='cancelled')
    except Exception as ex:
        log.error('lab job failed: ' + traceback.format_exc()); job.update(status='error', error=str(ex))


def list_backtests():
    out = []
    for f in sorted(glob.glob(os.path.join(DATA, 'backtests', '*.json')), key=os.path.getmtime, reverse=True):
        try:
            r = json.load(open(f))
            if r.get('lab'):
                out.append(dict(id=r['id'], name=r.get('name'), created=r.get('created'), lab=r.get('kind'), stats={}, period=[], sleeves=[], tf=''))
                continue
            out.append(dict(id=r['id'], name=r['name'], created=r['created'], stats=r['stats'], period=r['period'], years=r.get('years', {}),
                            engine=r.get('engine', 'v2'), oos=r.get('oos'),
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
    if out:   # FBL-BT01 (label only): results with a DCA slot predate the backtester's intrabar path fix
        out['unverified'] = dict(label=UNVERIFIED_BT01, applies_to='every row that includes a DCA (dca_dip) slot')
    return out


# ------------------------------------------------------------------ coin logos
# Bundled SVGs (CC0 cryptocurrency-icons + MIT web3icons) for the top coins; anything else is
# fetched once from Binance's public asset list and cached in DATA/icons.
_ICON_FAIL, _ASSETS = {}, {'t': 0, 'map': {}}
_ICON_LOCK = threading.Lock()


ICON_HOSTS = re.compile(r'^https://([a-z0-9-]+\.)*(bnbstatic\.com|binance\.com)/')


def _img_type(b):
    if b[:8] == b'\x89PNG\r\n\x1a\n': return 'image/png'
    if b[:3] == b'\xff\xd8\xff': return 'image/jpeg'
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP': return 'image/webp'
    return None                                      # SVG / HTML / anything else from the network is refused


def coin_icon(sym):
    base = ''.join(ch for ch in sym.upper().replace('.SVG', '').replace('.PNG', '') if ch.isalnum())[:24]
    if base.endswith('USDT'): base = base[:-4]
    for pre in ('1000000', '1000'):
        if base.startswith(pre) and len(base) > len(pre): base = base[len(pre):]
    if not base: return None
    f = os.path.join(BUNDLE, 'research', 'icons', base + '.svg')
    if os.path.exists(f): return open(f, 'rb').read(), 'image/svg+xml'          # bundled, reviewed files
    cdir = os.path.join(DATA, 'icons'); os.makedirs(cdir, exist_ok=True)
    f = os.path.join(cdir, base + '.img')
    if os.path.exists(f):
        b = open(f, 'rb').read(); t = _img_type(b)
        if t: return b, t
    if time.time() - _ICON_FAIL.get(base, 0) < 3600: return None
    with _ICON_LOCK:
        try:
            import requests
            if time.time() - _ASSETS['t'] > 3600 and not _ASSETS['map']:
                _ASSETS['t'] = time.time()
                try:
                    r = requests.get('https://www.binance.com/bapi/asset/v2/public/asset/asset/get-all-asset', timeout=8).json()
                    _ASSETS['map'] = {str(a.get('assetCode', '')).upper(): (a.get('logoUrl') or a.get('fullLogoUrl')) for a in r.get('data', []) if a.get('logoUrl') or a.get('fullLogoUrl')}
                except Exception as e:
                    log.info(f'coin logo list unavailable: {type(e).__name__}')
            urls = [u for u in (_ASSETS['map'].get(base), f'https://bin.bnbstatic.com/static/assets/logos/{base}.png') if u and ICON_HOSTS.match(u)]
            for u in urls:
                try:
                    r = requests.get(u, timeout=8, stream=True)
                    b = r.raw.read(300_000, decode_content=True) if r.ok else b''
                    t = _img_type(b)
                    if t and len(b) < 300_000:
                        open(f, 'wb').write(b); return b, t
                except Exception:
                    pass
        except Exception as e:
            log.info(f'coin logo {base}: {type(e).__name__}')
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
        self.tg = TelegramControl(lambda: self.engine, lambda: self)
        self.tg.start()

    def start_engine(self):
        old = self.engine
        if old is not None: old.lock.acquire()          # let a running cycle finish and save before the new engine reads state
        try:
            cfg = load_cfg()
            if cfg.pop('_plain', False):
                try: write_cfg({}); log.info('API keys are now stored encrypted (Windows DPAPI)')
                except Exception as e: log.warning(f'could not encrypt stored keys: {e}')
            self.cfg = cfg
            eng = Engine(cfg, DATA)
            tok = eng.S.get('TELEGRAM_TOKEN', '')            # v2 kept the Telegram token in settings.json -> move to the encrypted config
            if tok and set(tok) != {'•'}:
                try:
                    if not cfg.get('TELEGRAM_TOKEN'): write_cfg({'TELEGRAM_TOKEN': tok}); cfg['TELEGRAM_TOKEN'] = tok
                    eng.S.pop('TELEGRAM_TOKEN', None); eng.save_settings()
                except Exception as ex:
                    log.warning(f'Telegram token not migrated ({ex}) - re-enter it in Settings')
            try:
                eng.connect()
            except Exception as e:
                self._connect_failed(eng, e)
            self.engine = eng
        finally:
            if old is not None: old.lock.release()
        log.info(f"ZackBot {VERSION} | mode={'LIVE' if eng.live else 'PAPER'} | keys={'yes' if cfg.get('API_KEY') else 'no'} | data: {DATA}")
        self.preview()          # signals preview on start (no trading)

    CONNECT_RETRY_MIN, CONNECT_RETRY_MAX = 5.0, 60.0

    def _connect_failed(self, eng, ex, now=None):
        """T05b: start-up connect failed. Binance unreachable (transient) -> one exchange-down incident and an automatic,
        backed-off retry (5 s doubling to 60 s, jittered, never sooner than the outage circuit's next probe). Nothing is
        inferred from the failed read: no rules/positions/stops are assumed; the loop stays idle until connect succeeds and
        the incident is closed only by the later successful reconcile / account read. Other errors keep the old behaviour."""
        now = time.time() if now is None else now
        if not eng._exchange_down(ex):
            if eng.connect_retry:                       # Binance answers again but refuses (e.g. key): outage is over
                eng.resolve('exchange-down', f'Binance answering again - connect refused: {str(ex)[:80]}')
            eng.connect_retry = None
            eng.error = f'Could not connect to Binance: {ex}'
            log.error(eng.error)
            return
        r = eng.connect_retry or dict(n=0, since=now)
        r['n'] += 1
        delay = min(self.CONNECT_RETRY_MAX, self.CONNECT_RETRY_MIN * 2 ** min(r['n'] - 1, 6)) * random.uniform(0.8, 1.2)  # exponent capped first: no float overflow after a long outage
        try: floor = float(getattr(ex, 'retry_in', 0) or 0)
        except (TypeError, ValueError): floor = 0.0
        r['next_t'] = now + min(self.CONNECT_RETRY_MAX * 1.2, max(delay, floor if math.isfinite(floor) else 0.0))
        eng.connect_retry = r
        eng.error = f'Could not connect to Binance: {EXCHANGE_DOWN} - retrying automatically'
        eng.exchange_down_incident('start-up connect')

    def _reconnect(self, e, now=None):
        """T05b: retry a start-up connect that failed because Binance was unreachable. Same process, no restart."""
        r = e.connect_retry
        now = time.time() if now is None else now
        if e.connected or not r or now < r['next_t']: return False
        with e.lock:                                    # held across the connect, like start_engine (panel reads may wait)
            if self.engine is not e: return False       # replaced while waiting: the new engine connects itself
            try:
                e.connect()
            except Exception as ex:
                self._connect_failed(e, ex, now)
                return False
            e.connect_retry = None; e.error = None
        log.info(f"Binance answering again - connected after {int(now - r['since'])}s ({r['n']} failed start-up attempts); "
                 'the outage incident closes only once positions and stops were re-read')
        self.preview()
        return True

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
        e0 = self.engine
        last_bar, last_manage, last_eq, last_guard = {}, 0, 0, 0
        while True:
            try:
                e = self.engine
                if e is not e0: last_bar, e0 = {}, e                      # engine restarted (keys/mode changed)
                if e and not e.connected and getattr(e, 'connect_retry', None):   # T05b: start-up outage -> retry here
                    self._reconnect(e)
                    if self.engine is not e: e = None               # replaced (settings saved) during the retry: never
                                                                    # finish this pass on the old engine; next pass = new one
                if e and e.connected and e.cfg.get('API_KEY') and not e.error:
                    now = time.time()
                    tfs = sorted({sl['tf'] for sl in e.S['SLEEVES']} | {l.get('tf', '4h') for l in e.state['lots'].values()}
                                 | {g.get('tf', '4h') for g in e.S.get('GRID_SLOTS', []) if g.get('enabled')})
                    for tf in tfs:
                        bar = math.floor((now - 15) / TF_SEC[tf])
                        if tf not in last_bar:                              # start-up: catch up a candle missed while the app was off
                            lc = (e.state.get('last_cycle') or {}).get(tf)
                            try: done = datetime.fromisoformat(lc).timestamp() >= bar * TF_SEC[tf] + 10 if lc else False
                            except ValueError: done = False
                            last_bar[tf] = bar if done else bar - 1
                            if not done and lc: log.info(f'{tf}: last cycle {lc} - catching up the missed candle now')
                            elif not lc: last_bar[tf] = bar                 # brand-new install: wait for the next candle
                        if bar != last_bar[tf]:
                            try:
                                e.cycle(tf); last_bar[tf] = bar             # only marked done after it ran
                            except Exception as ex:
                                e.err(f'{tf} cycle failed: {ex} - retrying in 60s'); time.sleep(60)
                            self.preview()
                    if e.run_now.is_set():
                        e.run_now.clear()
                        for tf in tfs:
                            try: e.cycle(tf, 'manual run')
                            except Exception as ex: e.err(f'{tf} manual cycle failed: {ex}')
                        self.preview()
                    if now - last_manage > 8:
                        last_manage = now
                        try:
                            marks = e.data.marks()
                            if e.state['lots'] or e.state.get('grids') or e.state.get('pending_entries') or e.state.get('resting_entries'): e.manage(marks)
                            else: e.marks, e.marks_t = marks, now
                        except Exception as ex:                     # T05b final: Binance down -> the one exchange-down incident
                            if e._exchange_down(ex): e._manage_failed(f'mark prices: {EXCHANGE_DOWN}', key='exchange-down')
                            else: e._manage_failed(f'mark prices: {ex}')
                    if now - last_guard > 60:                               # daily halt / drawdown checked between candles too
                        last_guard = now
                        with e.lock:
                            try:
                                e.equity(); e.check_guards()
                                if not (e.state['lots'] or e.state.get('grids') or e.state.get('pending_entries')
                                        or e.state.get('resting_entries')):   # nothing to reconcile: account read = recovered
                                    e.resolve('exchange-down', 'Binance answering again - account readable')
                            except Exception as ex:                     # T05b: Binance down -> guards simply retry next pass
                                if not e._exchange_down(ex): raise
                                e.exchange_down_incident('daily guards')
                    if now - last_eq > 300:
                        last_eq = now
                        with e.lock:
                            try: e.record_equity(e.guard_eq or e.equity())
                            except Exception as ex:
                                if not e._exchange_down(ex): raise
                                e.exchange_down_incident('equity record')
                    e.next_cycle = {tf: datetime.fromtimestamp((math.floor(now / TF_SEC[tf]) + 1) * TF_SEC[tf] + 15, timezone.utc).isoformat(timespec='seconds') for tf in tfs}
                    self.loop_ok = time.time()
            except Exception as ex:
                log.error(f'main loop: {ex}')
                time.sleep(20)
            time.sleep(2)

    loop_ok = 0.0
    _orders = (0, [])

    # ---------------- read models for the panel (cheap; no exchange calls - prices come from the 8s mark cache)
    def _lots_view(self, e):
        marks = e.marks or {}
        with e.lock: items = [(k, dict(l)) for k, l in e.state['lots'].items()]
        lots = []
        for k, l in items:
            m = marks.get(l['symbol']); sd = 1 if l['side'] == 'LONG' else -1
            pnl = sd * (m - l['avg']) * l['qty'] if m else None
            lots.append(dict(key=k, **{x: l.get(x) for x in ('symbol', 'side', 'sleeve', 'qty', 'avg', 'e0', 'stop', 'opened', 'adds', 'dca', 'tp1', 'manual', 'tf', 'risk_usd')},
                             tp=l.get('tp'), protected=bool(l.get('stop_id')) and not l.get('stop_dirty'), add_blocked=l.get('add_blocked'),
                             exit=_safe(lambda: e.exit_plan(l)),
                             mark=m, pnl=pnl, r=(pnl / l['risk_usd']) if (pnl is not None and l.get('risk_usd')) else None,
                             risk_to_stop=sd * ((m or l['avg']) - l['stop']) * l['qty'], notional=(m or l['avg']) * l['qty']))
        return lots

    def health(self, e, lots):
        def ms_iso(t):
            try: return datetime.fromisoformat(t).timestamp()
            except Exception: return 0.0
        h = e.health
        if hasattr(e, '_sweep_incidents'): e._sweep_incidents()        # T05b: one-off errors stop being "open" when idle
        now = time.time()
        mt = max(e.marks_t or 0, 0)
        exch = 'error' if e.error else ('ok' if e.connected and now - mt < 60 else 'stale')
        unprot = [l['key'] for l in lots if not l['protected']]
        lm = h.get('last_manage_ok')
        engine = 'stopped' if now - (self.loop_ok or 0) > 60 else ('degraded' if (h['manage_fail_streak'] >= 3 or unprot or e.untracked or e.state.get('orphans')) else 'ok')
        entries = 'halted' if e.state.get('halted') else 'paused' if e.S.get('ENTRIES_PAUSED') else 'open'
        return dict(engine=engine, exchange=exch, last_sync=h.get('last_sync'), last_prices=datetime.fromtimestamp(mt, timezone.utc).isoformat(timespec='seconds') if mt else None,
                    last_manage_ok=lm, last_cycle_ok=h.get('last_cycle_ok'), errors=list(h['errors'])[-12:][::-1], unprotected=unprot,
                    untracked=e.untracked, orphans=len(e.state.get('orphans') or []), entries=entries, next_reset=next_reset_utc(),
                    fail_streak=h['manage_fail_streak'], lev_refusals={k: dict(v) for k, v in getattr(e, 'lev_refusals', {}).items()},
                    fills=e.fill_summary() if hasattr(e, 'fill_summary') else None,
                    exchange_circuit=e.exchange_state() if hasattr(e, 'exchange_state') else None,      # T05b
                    confirmed=dict(h.get('confirmed') or {}),
                    incidents=[{k: v for k, v in i.items() if k not in ('entry', 'logged')} for i in sorted(
                        [i for i in list((h.get('incidents') or {}).values()) if i.get('open')],   # snapshot: loop thread mutates
                        key=lambda i: (i.get('key') != 'exchange-down', -ms_iso(i.get('last'))))][:20],
                    audit=e.audit_summary() if hasattr(e, 'audit_summary') else None)

    def revs(self, e):
        hl = e.history[-1]['id'] if e.history else ''
        ml = e.missed[-1]['logged'] if e.missed else ''
        eh = e.equity_hist[-1][0] if e.equity_hist else 0
        return dict(history=f'{len(e.history)}:{hl}', missed=f'{len(e.missed)}:{ml}', signals=str(e.signals_time),
                    equity=f'{len(e.equity_hist)}:{eh}', meta=VERSION)

    def snapshot(self):
        e = self.engine
        lots = self._lots_view(e)
        st = e.state
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
        settings = {k: v for k, v in e.S.items() if k not in ('TELEGRAM_TOKEN', 'TELEGRAM_PIN')}
        settings['TELEGRAM_TOKEN_SET'] = bool(e.cfg.get('TELEGRAM_TOKEN'))
        settings['TELEGRAM_PIN_SET'] = bool(e.S.get('TELEGRAM_PIN'))
        extra = {}
        for name, fn in (('risk_rules', e.risk_rules_status), ('governor', e.governor_status), ('grids', e.grids.status)):
            try: extra[name] = fn()
            except Exception as ex: extra[name] = dict(error=str(ex)[:120])
        extra['telegram'] = self.tg.status() if getattr(self, 'tg', None) else None
        return dict(version=VERSION, build=BUILD_ID, mode='LIVE' if e.live else 'PAPER', keys=bool(e.cfg.get('API_KEY')), ai_key=bool(e.cfg.get('ANTHROPIC_API_KEY')),
                    error=e.error, hedge=e.hedge, equity=e.guard_eq if e.guard_eq is not None else e.last_eq, sizing_equity=e.last_eq,
                    balance=e.last_balance, day_start=st.get('day_start_equity'), peak=st.get('peak_equity'), halted=st.get('halted'),
                    last_cycle=st.get('last_cycle'), next_cycle=getattr(e, 'next_cycle', None), settings=settings, lots=lots,
                    signals_time=e.signals_time, exposure=exposure, capital=e.capital_info(), health=self.health(e, lots), rev=self.revs(e),
                    jobs={k: {x: y for x, y in j.items() if x != 'result'} for k, j in list(JOBS.items())[-12:]}, **extra)

    def meta(self):
        e = self.engine
        return dict(version=VERSION, build=BUILD_ID, presets={k: dict(name=v['name'], note=v['note'], bt=v.get('bt'), sleeves=v['sleeves']) for k, v in PRESETS.items()},
                    library={k: dict(name=v['name'], style=v['style'], sides=v['sides'], desc=v['desc'], mgmt=v['mgmt']) for k, v in S.STRATEGIES.items()},
                    core8=CORE8, top40=TOP40, tradable=sorted(e.rules) if e.rules else [], manual_max_risk=MANUAL_MAX_RISK * 100,
                    risk_rule_defaults=RISK_RULE_DEFAULTS, grid_defaults=GRID.clean_cfg({}), gov_mult_max=GOV_MULT_MAX)

    def preflight(self):
        """BT02: exchange-filter preflight of every profile (and the current slots) at the current capital, through the
        same feasibility functions the engine's entry gate and the backtester use. Prices / ATR: the engine's cached closed
        candles when it has them, else the candle files shipped with the app (data/, data1h/) - the result says which."""
        e = self.engine
        snap, st, detail = exchange_rules_now(e)
        rules = F.snapshot_rules(snap) or {}
        try: capital = float(e.capital_info()['capital'])
        except Exception: capital = float(e.S.get('CAPITAL_CAP') or 0)
        if capital <= 0: capital = float(e.S.get('CAPITAL_CAP') or 500)
        uni = [s for s in (e.S.get('UNIVERSE') or TOP40)]
        sets = {k: v['sleeves'] for k, v in PRESETS.items()}
        sets['__current'] = e.S.get('SLEEVES') or []
        slots_by = {}
        for k, sl in sets.items():
            out = []
            for x in sl:
                if not x.get('enabled', True): continue
                sy = x.get('symbols', 'all')
                sy = CORE8 if sy == 'core8' else uni if sy == 'all' else list(sy)
                out.append(dict(id=x.get('id'), key=x['key'], share=float(x['share']), risk=float(x['risk']), tf=x.get('tf') or '4h',
                                symbols=list(sy), mgmt=S.merge_mgmt(x['key'], x.get('mgmt')),
                                sides=x.get('sides') or (S.STRATEGIES.get(x['key']) or {}).get('sides') or 'both'))
            slots_by[k] = out
        need = {(s_, x['tf']) for v in slots_by.values() for x in v for s_ in x['symbols']}
        market, asof = preflight_market(e, need)
        lev = float(e.S.get('MAX_LEVERAGE') or 10)
        res = {k: F.preflight(v, capital, market, rules, st, detail, lev) for k, v in slots_by.items()}
        # planning estimate (typical volatility): the same check with the 180-candle median ATR - never the headline status
        typical = {k: dict(v, atr=v['atr_median']) for k, v in market.items()}
        for k, v in slots_by.items():
            p = F.preflight(v, capital, typical, rules, st, detail, lev)
            res[k]['planning'] = dict(basis='median ATR of the last 180 closed candles', status=p['status'], estimate=p['estimate'],
                                      plan_executable_pct=p['plan_executable_pct'], min_capital_all=p['min_capital_all'])
        return dict(capital=capital, rules_state=st, rules_detail=detail, environment=(snap or {}).get('environment'),
                    rules_source=(snap or {}).get('source'), rules_fetched_at=(snap or {}).get('fetched_at'),
                    market_basis='latest closed candle (price and ATR), as the engine sizes the next signal',
                    market_asof=asof, presets=res)

    def missed_view(self):
        e = self.engine; marks = e.marks or {}
        out = []
        for m in e.missed[-300:][::-1]:
            mk = marks.get(m['symbol']); sd = 1 if m['side'] == 'LONG' else -1
            out.append(dict(m, now=mk, move_pct=round(sd * (mk - m['price']) / m['price'] * 100, 2) if mk else None))
        return out

    def orders_view(self):
        e = self.engine
        try: mt = os.path.getmtime(e.F['trades'])
        except OSError: return []
        if self._orders[0] != mt:
            with open(e.F['trades']) as f: rows = list(csv.DictReader(f))
            self._orders = (mt, rows[-300:][::-1])
        return self._orders[1]

    def candles_view(self, sym, tf, n):
        """Candles + markers for the price chart of one coin (entries, exits, current stop/targets)."""
        e = self.engine
        if not re.fullmatch(r'[A-Z0-9]{2,20}USDT', sym or '') or tf not in TF_SEC: raise ValueError('bad symbol/timeframe')
        df = e.candles(sym, tf).tail(max(30, min(int(n), 400)))
        bars = [[int(t.timestamp()), round(o, 10), round(h, 10), round(l, 10), round(c, 10)] for t, o, h, l, c in zip(df.t, df.o, df.h, df.l, df.c)]
        t0 = bars[0][0] if bars else 0
        fills = []
        for h in e.history[-1500:]:
            if h['symbol'] != sym: continue
            for f in h.get('fills') or []:
                ts = datetime.fromisoformat(f[0]).timestamp()
                if ts >= t0: fills.append(dict(t=int(ts), kind=f[1], qty=f[2], px=f[3], side=h['side'], sleeve=h['sleeve']))
        lines = []
        with e.lock:
            for l in e.state['lots'].values():
                if l['symbol'] != sym: continue
                for f in l.get('fills') or []:
                    ts = datetime.fromisoformat(f[0]).timestamp()
                    if ts >= t0: fills.append(dict(t=int(ts), kind=f[1], qty=f[2], px=f[3], side=l['side'], sleeve=l['sleeve']))
                lines.append(dict(kind='avg', px=l['avg'], side=l['side'], sleeve=l['sleeve']))
                lines.append(dict(kind='stop', px=l['stop'], side=l['side'], sleeve=l['sleeve']))
                if l.get('tp'): lines.append(dict(kind='target', px=l['tp'], side=l['side'], sleeve=l['sleeve']))
        return dict(symbol=sym, tf=tf, bars=bars, fills=fills, lines=lines, mark=(e.marks or {}).get(sym))

    def log_tail(self, n=250):
        try:
            with open(LOG_F, 'rb') as f:
                f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 96_000))
                lines = f.read().decode('utf-8', 'replace').splitlines()[-n:]
        except Exception: lines = []
        return lines[::-1]

    def test_connection(self):
        e = self.engine
        out = dict(mode='LIVE' if e.live else 'PAPER (Binance Futures testnet)', checks=[])
        add = lambda ok, name, detail='': out['checks'].append(dict(ok=ok, name=name, detail=detail))
        if not e.cfg.get('API_KEY'): add(False, 'API key saved', 'add your keys below'); return out
        try:
            t0 = time.time(); e.trade.sync_time(); add(True, 'Binance reachable', f'{(time.time() - t0) * 1000:.0f} ms, clock offset {e.trade.offset} ms')
        except Exception as ex: add(False, 'Binance reachable', str(ex)[:120]); return out
        try:
            acc = e.trade.account()
            add(bool(acc.get('canTrade', True)), 'Key can trade futures', f"balance {float(acc.get('totalMarginBalance', 0)):.2f} USDT")
        except Exception as ex: add(False, 'Key can trade futures', str(ex)[:140]); return out
        try: add(e.trade.hedge_mode(), 'Hedge mode (longs + shorts together)', 'on' if e.hedge else 'off - shorts are skipped')
        except Exception as ex: add(False, 'Hedge mode', str(ex)[:120])
        if e.live:
            try:
                r = e.trade.api_restrictions() or {}
                add(not r.get('enableWithdrawals', False), 'Withdrawals DISABLED on this key', 'ENABLED - turn it off on Binance now!' if r.get('enableWithdrawals') else 'good')
                add(bool(r.get('ipRestrict')), 'Key restricted to your IP', 'recommended' if not r.get('ipRestrict') else 'good')
                add(bool(r.get('enableFutures', True)), 'Futures permission', '')
            except Exception as ex: add(False, 'Key permissions', f'could not read: {str(ex)[:100]}')
        else:
            add(True, 'Paper mode', 'orders go to the Binance testnet - no real money')
        return out

    def research_get(self):
        return self.research


APP = None
TOKEN = secrets.token_urlsafe(32)
HOSTS = (f'127.0.0.1:{PORT}', f'localhost:{PORT}')
ORIGINS = (f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}')
SEC_HEADERS = {'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
               'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                          "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}
LOCKED_PAGE = ('<!doctype html><meta charset=utf-8><title>ZackBot</title><body style="background:#0b0920;color:#ddd;font:15px Segoe UI,sans-serif;'
               'display:grid;place-items:center;height:90vh"><div><h2>ZackBot is running</h2><p>For security, the control panel only opens from the '
               'ZackBot shortcut. Close this tab and start ZackBot again from the desktop or Start menu.</p></div>')


# ------------------------------------------------------------------ HTTP (loopback only, per-launch token, exact Host/Origin)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body, ctype='application/json', cache='no-store', extra=None):
        b = body.encode() if isinstance(body, str) else body
        gz = len(b) > 1400 and 'gzip' in (self.headers.get('Accept-Encoding') or '') and ctype.startswith(('application/json', 'text/'))
        if gz: b = gzip.compress(b, 5)
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(b)))
        if gz: self.send_header('Content-Encoding', 'gzip')
        self.send_header('Cache-Control', cache)
        for k, v in {**SEC_HEADERS, **(extra or {})}.items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str))

    def _authed(self):
        tok = self.headers.get('X-ZB-Token', '')
        for part in (self.headers.get('Cookie') or '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == 'zb': tok = tok or v
        return bool(tok) and hmac.compare_digest(tok.encode('utf-8', 'surrogateescape'), TOKEN.encode())

    def _host_ok(self):
        return self.headers.get('Host', '') in HOSTS

    def do_GET(self):
        if not self._host_ok(): return self._send(421, '{}')
        u = urlsplit(self.path); p, q = u.path, parse_qs(u.query)
        if p in ('/', '/index.html'):
            t = (q.get('t') or [''])[0]
            if t and hmac.compare_digest(t.encode('utf-8', 'surrogateescape'), TOKEN.encode()):            # launch link -> session cookie, then a clean URL
                self.send_response(303); self.send_header('Location', '/')
                self.send_header('Set-Cookie', f'zb={TOKEN}; HttpOnly; SameSite=Strict; Path=/')
                for k, v in SEC_HEADERS.items(): self.send_header(k, v)
                self.send_header('Content-Length', '0'); self.end_headers(); return
            if not self._authed(): return self._send(401, LOCKED_PAGE, 'text/html; charset=utf-8')
            return self._send(200, open(os.path.join(BUNDLE, 'panel.html'), encoding='utf-8').read(), 'text/html; charset=utf-8')
        if p == '/api/ping':          # proves it is THIS ZackBot: answers a nonce with HMAC(token, nonce)
            nonce = (q.get('nonce') or [''])[0][:64]
            proof = hmac.new(TOKEN.encode(), nonce.encode(), 'sha256').hexdigest() if nonce else ''
            return self._json(dict(app='zackbot', version=VERSION, build=BUILD_ID, proof=proof))
        if not self._authed(): return self._json(dict(ok=False, error='not authorised - reopen ZackBot from its shortcut'), 401)
        try:
            if p.startswith('/icon/'):
                r = coin_icon(p[6:])
                ext = {'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'"}
                if r: return self._send(200, r[0], r[1], 'private, max-age=86400', ext)
                return self._send(404, b'', 'image/png', 'private, max-age=600', ext)
            if p == '/api/status': return self._json(APP.snapshot())
            if p == '/api/meta': return self._json(APP.meta())
            if p == '/api/preflight': return self._json(APP.preflight())
            if p == '/api/audit_summary':     # T05a: read-only trade-audit summary (cached; no engine lock held)
                e = APP.engine
                return self._json(e.audit_summary() if hasattr(e, 'audit_summary') else dict(error='no audit'))
            if p == '/api/history': return self._json(dict(rev=APP.revs(APP.engine)['history'], history=APP.engine.history[-1500:]))
            if p == '/api/missed': return self._json(dict(rev=APP.revs(APP.engine)['missed'], missed=APP.missed_view()))
            if p == '/api/signals':
                e = APP.engine
                return self._json(dict(rev=str(e.signals_time), signals=e.signals, states=APP.states, time=e.signals_time))
            if p == '/api/equity': return self._json(dict(rev=APP.revs(APP.engine)['equity'], equity_hist=APP.engine.equity_hist[-3000:]))
            if p == '/api/orders': return self._json(APP.orders_view())
            if p == '/api/logs': return self._json(APP.log_tail())
            if p == '/api/candles':
                return self._json(APP.candles_view((q.get('symbol') or [''])[0], (q.get('tf') or ['4h'])[0], (q.get('n') or ['160'])[0]))
            if p == '/api/research': return self._json(APP.research)
            if p == '/api/backtests': return self._json(list_backtests())
            if p.startswith('/api/backtest/'):
                bid = p.rsplit('/', 1)[1]
                if bid in JOBS: return self._json({k: v for k, v in JOBS[bid].items()})
                if not BT_ID.match(bid): return self._json(dict(status='missing'), 404)
                f = os.path.join(DATA, 'backtests', f'{bid}.json')
                if os.path.exists(f): return self._json(dict(status='done', result=json.load(open(f))))
                return self._json(dict(status='missing'), 404)
        except ValueError as ex:
            return self._json(dict(ok=False, error=str(ex)), 400)
        except Exception as ex:
            log.error(f'GET {p}: {ex}'); return self._json(dict(ok=False, error=str(ex)), 500)
        self._send(404, '{}')

    def do_POST(self):
        if not self._host_ok(): return self._send(421, '{}')
        origin = self.headers.get('Origin')
        if origin is not None and origin not in ORIGINS:
            return self._json(dict(ok=False, error='forbidden origin'), 403)
        if not self._authed(): return self._json(dict(ok=False, error='not authorised - reopen ZackBot from its shortcut'), 401)
        if (self.headers.get('Content-Type') or '').split(';')[0].strip().lower() != 'application/json':
            return self._json(dict(ok=False, error='JSON only'), 415)
        try: n = int(self.headers.get('Content-Length') or 0)
        except ValueError: n = -1
        if not 0 <= n <= 1_000_000: return self._json(dict(ok=False, error='bad request size'), 413)
        try:
            body = json.loads(self.rfile.read(n) or b'{}')
            if not isinstance(body, dict): raise ValueError('bad request')
            self._json(dict(ok=True, msg=handle(self.path, body)))
        except (ValueError, KeyError, TypeError) as e:
            log.warning(f'panel action rejected: {e}'); self._json(dict(ok=False, error=str(e)), 400)
        except Exception as e:
            log.error('panel action failed: ' + traceback.format_exc()); self._json(dict(ok=False, error=str(e)), 400)


# ------------------------------------------------------------------ request validation
LIMITS = dict(MAX_LEVERAGE=(1, 50), DAILY_LOSS_HALT=(0.01, 1.0), PEAK_DD_FLATTEN=(0.0, 0.95))
SYM = re.compile(r'^[A-Z0-9]{2,20}USDT$')
MG_NUM = dict(stop_atr=(0.3, 20), trail_atr=(0.3, 20), be_r=(0.1, 50), tp1_r=(0.1, 50), tp1_frac=(0.05, 1), tp_r=(0.2, 100), max_bars=(1, 5000))
MG_SUB = dict(pyramid=dict(n=(0, 10), step_r=(0.1, 20), frac=(0.05, 5)),
              dca=dict(n=(0, 8), step_atr=(0.1, 20), scale=(0.1, 3), tp_atr=(0.1, 20), stop_atr=(0.1, 30)),
              ttp=dict(at_r=(0.2, 50), dev_pct=(0.1, 50)),
              runner=dict(be_r=(0.1, 100), step_r=(0.1, 100), gap_r=(0.1, 100), giveback=(0.05, 1), gb_from=(0.1, 100), dca_frac=(0.05, 1), trend_exit=(0, 1)))


def _num(v, lo, hi, name):
    try: x = float(v)
    except (TypeError, ValueError): raise ValueError(f'{name} must be a number')
    if not (lo <= x <= hi) or x != x: raise ValueError(f'{name} must be between {lo} and {hi}')
    return x


def clean_mgmt(m):
    if not isinstance(m, dict): raise ValueError('bad trade management settings')
    out = {}
    for k, v in m.items():
        if v is None: continue
        if k in MG_NUM:
            if k in ('be_r', 'tp1_r', 'tp_r', 'max_bars', 'trail_atr') and v in (0, '0', 0.0): out[k] = 0; continue   # 0 = off
            out[k] = _num(v, *MG_NUM[k], k)
        elif k == 'tps':
            if not isinstance(v, list) or len(v) > 8: raise ValueError('take-profit ladder: up to 8 levels')
            lv = []
            for it in v:
                if not isinstance(it, (list, tuple)) or len(it) != 2: raise ValueError('each take-profit level is [R, fraction]')
                lv.append([_num(it[0], 0.1, 100, 'take-profit R'), _num(it[1], 0.01, 1, 'take-profit fraction')])
            if sum(x[1] for x in lv) > 1.0001: raise ValueError('take-profit fractions add up to more than 100%')
            if lv: out[k] = sorted(lv)
        elif k in MG_SUB:
            if not isinstance(v, dict): raise ValueError(f'bad {k} settings')
            out[k] = {kk: _num(vv, *MG_SUB[k][kk], f'{k}.{kk}') for kk, vv in v.items() if kk in MG_SUB[k] and vv is not None}
            for kk in ('n',):
                if kk in out[k]: out[k][kk] = int(out[k][kk])
        else: raise ValueError(f'unknown setting {k}')
    return out


def clean_symbols(v):
    if v in ('all', 'core8'): return v
    if not isinstance(v, list) or len(v) > 80: raise ValueError('coin list must be all, core8 or a list of up to 80 coins')
    for x in v:
        if not isinstance(x, str) or not SYM.match(x): raise ValueError(f'bad coin symbol: {str(x)[:20]}')
    return v


def clean_pump(v):
    if not isinstance(v, dict): raise ValueError('pump guard must be an object')
    out = {}
    if v.get('max_candle_atr') not in (None, '', 0): out['max_candle_atr'] = _num(v['max_candle_atr'], 0.5, 20, 'pump guard candle size')
    if v.get('btc_1h_pct') not in (None, '', 0): out['btc_1h_pct'] = _num(v['btc_1h_pct'], 0.2, 30, 'pump guard BTC move')
    return out


RR_PARAMS = dict(coin_cap=dict(x=(0.1, 50)), open_risk_cap=dict(pct=(0.5, 100)), correlated_cap=dict(n=(1, 20), rho=(0.3, 0.99)),
                 btc_breaker=dict(pct=(0.5, 30), hours=(0.25, 72)), funding_filter=dict(rate=(0.00005, 0.05)))


def clean_risk_rules(v):
    if not isinstance(v, dict): raise ValueError('risk rules must be an object')
    out = {}
    for rule, cfg in v.items():
        if rule not in RISK_RULE_DEFAULTS or not isinstance(cfg, dict): raise ValueError(f'unknown risk rule {str(rule)[:30]}')
        c = {}
        if 'mode' in cfg:
            if cfg['mode'] not in ('off', 'warn', 'enforce'): raise ValueError('rule mode must be off, warn or enforce')
            c['mode'] = cfg['mode']
        for k, rng in RR_PARAMS[rule].items():
            if cfg.get(k) is not None: c[k] = _num(cfg[k], *rng, f'{rule}.{k}')
        if rule == 'correlated_cap' and 'n' in c: c['n'] = int(c['n'])
        if rule == 'btc_breaker' and 'tighten' in cfg: c['tighten'] = bool(cfg['tighten'])
        if rule == 'btc_breaker' and 'dca' in cfg:
            if cfg['dca'] not in ('pause', 'half_size', 'continue_plan'): raise ValueError('breaker DCA policy must be pause, half_size or continue_plan')
            c['dca'] = cfg['dca']
        out[rule] = c
    return out


def clean_governor(v):
    if not isinstance(v, dict): raise ValueError('governor must be an object')
    mode = v.get('mode', 'off')
    if mode not in ('off', 'suggest', 'auto'): raise ValueError('governor mode must be off, suggest or auto')
    rules = v.get('rules') or []
    if not isinstance(rules, list) or len(rules) > 8: raise ValueError('up to 8 governor rules')
    out = []
    for r in rules:
        if not isinstance(r, dict) or r.get('if') not in ('growth_gte', 'dd_gte'): raise ValueError('rule condition must be growth_gte or dd_gte')
        then = r.get('then') if isinstance(r.get('then'), dict) else {}
        if 'risk_mult' in then: then = dict(risk_mult=_num(then['risk_mult'], 0.05, GOV_MULT_MAX, 'risk multiplier'))
        elif then.get('profile') in PRESETS: then = dict(profile=then['profile'])
        else: raise ValueError('rule action must be a risk multiplier or a profile')
        if r.get('until') not in (None, 'new_high', 'reset'): raise ValueError('until must be new_high, reset or empty')
        out.append({'if': r['if'], 'value': _num(r.get('value'), 0.5, 1000, 'rule threshold'), 'then': then, 'until': r.get('until')})
    return dict(mode=mode, rules=out)


def validate_sleeve(sl, i):
    if not isinstance(sl, dict) or sl.get('key') not in S.STRATEGIES: raise ValueError(f'unknown strategy {str(sl.get("key"))[:30]}')
    sid = str(sl.get('id') or f'S{i + 1}')[:12]
    if not re.fullmatch(r'[A-Za-z0-9_\-]{1,12}', sid): raise ValueError('slot names: letters, digits, - and _ only (max 12)')
    if sid == 'MAN': raise ValueError('MAN is reserved for manual trades')
    out = dict(id=sid, key=sl['key'], name=S.STRATEGIES[sl['key']]['name'],
               enabled=bool(sl.get('enabled', True)), share=_num(sl['share'], 0.001, 1, 'capital share'), risk=_num(sl['risk'], 0.0005, 0.25, 'risk per trade'),
               max_pos=int(_num(sl['max_pos'], 1, 20, 'max positions')), symbols=clean_symbols(sl.get('symbols', 'all')),
               sides=sl.get('sides') or S.STRATEGIES[sl['key']]['sides'], mgmt=clean_mgmt(sl.get('mgmt') or {}), tf=sl.get('tf', '4h'))
    if sl.get('hours'): out['hours'] = sorted({int(_num(h, 0, 23, 'hour')) for h in sl['hours']})
    if sl.get('when') not in (None, 'any'):
        if sl['when'] not in ('bull', 'bear', 'range'): raise ValueError('market filter must be any, bull, bear or range')
        out['when'] = sl['when']
    if sl.get('trail_entry'):
        te = sl['trail_entry'] if isinstance(sl['trail_entry'], dict) else {}
        out['trail_entry'] = dict(dev_atr=_num(te.get('dev_atr', 0.5), 0.05, 10, 'trailing entry distance'),
                                  max_bars=int(_num(te.get('max_bars', 3), 1, 50, 'trailing entry candles')))
    if sl.get('pump_guard'): out['pump_guard'] = clean_pump(sl['pump_guard'])
    if 'dca' in out['mgmt'] and out['mgmt']['dca'].get('n') and not out['mgmt']['dca'].get('stop_atr') \
            and not S.STRATEGIES[sl['key']]['mgmt'].get('dca', {}).get('stop_atr'):
        raise ValueError('a DCA / martingale basket must keep a hard basket stop')
    if sl.get('vol_max_pct'): out['vol_max_pct'] = _num(sl['vol_max_pct'], 0.05, 1, 'volatility filter')
    if sl.get('kelly'):
        k = sl['kelly'] if isinstance(sl['kelly'], dict) else {}
        out['kelly'] = dict(frac=_num(k.get('frac', 0.5), 0.05, 1, 'Kelly fraction'), min=_num(k.get('min', 0.25), 0.05, 2, 'Kelly min'), max=_num(k.get('max', 2.0), 0.5, 4, 'Kelly max'))
    if out['sides'] not in ('long', 'short', 'both'): raise ValueError('direction must be long/short/both')
    if out['tf'] not in TF_SEC: raise ValueError('timeframe must be 15m, 1h or 4h')
    return out


def handle(path, b):
    e = APP.engine
    if path == '/api/settings':
        with e.lock:
            for k, v in b.items():
                if k in LIMITS:
                    e.S[k] = _num(v, *LIMITS[k], k)
                    if k == 'MAX_LEVERAGE': e._lev = {}                       # re-apply on the next entry per coin
                elif k == 'CAPITAL_CAP': e.set_capital_base(_num(v, 0, 1e9, 'start amount'))
                elif k in ('ENTRIES_PAUSED', 'AI_FILTER', 'RUN_IN_BACKGROUND', 'TELEGRAM_ON', 'MAKER_FALLBACK'): e.S[k] = bool(v)
                elif k == 'ENTRY_ORDER':
                    if v not in ('market', 'maker'): raise ValueError('entry order must be market or maker')
                    e.S[k] = v
                elif k == 'MAKER_REPRICE': e.S[k] = int(_num(v, 0, 10, 'maker re-prices'))
                elif k == 'MAKER_WAIT_S': e.S[k] = int(_num(v, 5, 300, 'maker wait seconds'))
                elif k == 'FEE_MAKER': e.S[k] = _num(v, -0.001, 0.002, 'maker fee')
                elif k == 'PUMP_GUARD': e.S[k] = clean_pump(v)
                elif k == 'RISK_RULES': e.S[k] = clean_risk_rules(v)
                elif k == 'GOVERNOR': e.S[k] = clean_governor(v)
                elif k in ('TELEGRAM_CONTROL', 'TELEGRAM_PIN'):
                    nv = tg_clean_setting(k, v, e.S.get(k)); e.S[k] = nv if nv is not None else e.S.get(k, '')
                elif k == 'TELEGRAM_TOKEN':
                    v = str(v).strip()
                    if v and set(v) != {'•'}:
                        if not cfg_value_ok('TELEGRAM_TOKEN', v): raise ValueError('that does not look like a Telegram bot token (123456:ABC...)')
                        write_cfg({'TELEGRAM_TOKEN': v}); e.cfg['TELEGRAM_TOKEN'] = v; APP.cfg['TELEGRAM_TOKEN'] = v
                elif k == 'TELEGRAM_CHAT':
                    v = str(v).strip()
                    if v and not re.fullmatch(r'-?\d{3,20}|@[A-Za-z0-9_]{4,40}', v): raise ValueError('chat id is a number like 123456789 or -100..., or @channelname')
                    e.S[k] = v
                elif k == 'SYMBOLS_ON':
                    if not isinstance(v, dict): raise ValueError('bad coin switches')
                    e.S['SYMBOLS_ON'].update({s: bool(x) for s, x in v.items() if s in e.S['UNIVERSE']})
                elif k == 'ADD_SYMBOL':
                    s = str(v).strip().upper(); s = s if s.endswith('USDT') else s + 'USDT'
                    if not SYM.match(s): raise ValueError('coin names are letters/digits, e.g. HYPE or 1000PEPE')
                    if not e.rules: raise ValueError('not connected to Binance - cannot check that coin right now')
                    if s not in e.rules: raise ValueError(f'{s} is not a Binance USDT perpetual')
                    if s not in e.S['UNIVERSE']: e.S['UNIVERSE'].append(s); e.S['SYMBOLS_ON'][s] = True
                elif k == 'REMOVE_SYMBOL':
                    if v in e.S['UNIVERSE']: e.S['UNIVERSE'].remove(v); e.S['SYMBOLS_ON'].pop(v, None)
                elif k == 'UNIVERSE':
                    if not isinstance(v, list) or not e.rules: raise ValueError('cannot replace the coin list right now')
                    e.S['UNIVERSE'] = [s for s in v if isinstance(s, str) and SYM.match(s) and s in e.rules][:120]
                    e.S['SYMBOLS_ON'] = {s: e.S['SYMBOLS_ON'].get(s, True) for s in e.S['UNIVERSE']}
                else:
                    raise ValueError(f'unknown setting {str(k)[:30]}')
            e.save_settings()
        log.info('settings changed from panel: ' + ', '.join(str(k) for k in b.keys()))
        if any(str(k).startswith('TELEGRAM') for k in b) and getattr(APP, 'tg', None): APP.tg.restart()
        APP.preview()
        return 'saved'
    if path == '/api/sleeves':
        if not isinstance(b.get('sleeves'), list) or len(b['sleeves']) > 12: raise ValueError('1-12 strategy slots')
        sl = [validate_sleeve(x, i) for i, x in enumerate(b['sleeves'])]
        if len({x['id'] for x in sl}) != len(sl): raise ValueError('each strategy slot needs a unique name')
        gshare = sum(g.get('share', 0) for g in e.S.get('GRID_SLOTS', []) if g.get('enabled'))
        if sum(x['share'] for x in sl if x['enabled']) + gshare > 1.0001: raise ValueError('capital shares of active strategies (and grids) add up to more than 100%')
        with e.lock:
            e.S['SLEEVES'] = sl; e.S['PRESET'] = 'custom'; e.save_settings()
        log.info('strategies updated from panel: ' + ', '.join(f"{x['id']}={x['key']}@{x['risk']:.1%}" + ('' if x['enabled'] else '(off)') for x in sl))
        APP.preview()
        return 'strategies saved'
    if path == '/api/preset':
        if b.get('name') not in PRESETS: raise ValueError('unknown profile')
        e.apply_preset(b['name']); APP.preview()
        return f"preset {PRESETS[b['name']]['name']} applied"
    if path == '/api/keys':
        upd = {}
        for k in ('API_KEY', 'API_SECRET', 'ANTHROPIC_API_KEY'):
            v = str(b.get(k) or '').strip()
            if v:
                if not cfg_value_ok(k, v): raise ValueError(f'{k.replace("_", " ").lower()}: unexpected characters - copy it again from Binance/Anthropic')
                upd[k] = v
        if b.get('MODE') in ('paper', 'live'):
            if b['MODE'] == 'live' and b.get('CONFIRM') != 'YES_REAL_MONEY': raise ValueError('type YES_REAL_MONEY to switch to live')
            upd['MODE'] = b['MODE']; upd['LIVE_CONFIRM'] = 'YES_REAL_MONEY' if b['MODE'] == 'live' else ''
        elif b.get('MODE') is not None: raise ValueError('mode must be paper or live')
        if upd.get('MODE') and upd['MODE'] != APP.cfg.get('MODE') and e.state['lots']:
            raise ValueError('close all open positions before switching paper/live')
        write_cfg(upd)
        with APP.lock: APP.start_engine()
        return 'saved - engine restarted'
    if path == '/api/backtest':
        jid = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
        req = dict(name=str(b.get('name') or 'Backtest')[:80], days=int(_num(b.get('days', 730), 30, 2200, 'period')),
                   tf=b.get('tf') if b.get('tf') in TF_SEC else '4h', start=_num(b.get('start', 500), 50, 1e8, 'start capital'),
                   max_lev=_num(b.get('max_lev', 10), 1, 50, 'max leverage'), daily_halt=_num(b.get('daily_halt', 0.08), 0.01, 1, 'daily halt'),
                   universe=[s for s in (b.get('universe') or e.S['UNIVERSE']) if isinstance(s, str) and SYM.match(s)][:80])
        if not isinstance(b.get('sleeves'), list) or not b['sleeves']: raise ValueError('no strategies to test')
        req['sleeves'] = [validate_sleeve(x, i) for i, x in enumerate(b['sleeves'][:12])]
        if b.get('run_options'): req['run_options'] = LAB._validate_run_options(b['run_options'])
        JOBS[jid] = dict(id=jid, status='queued')
        try: enqueue(run_backtest_job, jid, req)
        except ValueError: JOBS.pop(jid, None); raise
        return jid
    if path == '/api/study':
        days = int(_num(b.get('days', 1460), 90, 2200, 'period')); start = _num(b.get('start', 500), 50, 1e8, 'start')
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
        try: enqueue(study)
        except ValueError: JOBS.pop(sid, None); raise
        return sid
    if path == '/api/lab':
        kind = b.get('kind')
        if kind not in ('optimize', 'walk_forward', 'monte_carlo', 'lookahead', 'liquidation'): raise ValueError('unknown lab test')
        req = {k: v for k, v in b.items() if k != 'kind'}
        req.setdefault('universe', e.S['UNIVERSE'])
        LAB.validate_lab_request(kind, req)                      # fail fast with a readable message
        jid = datetime.now().strftime('%Y%m%d-%H%M%S-') + 'lab-' + kind.replace('_', '')
        JOBS[jid] = dict(id=jid, status='queued', kind=kind, progress=0.0, cancel=False)
        try: enqueue(run_lab, jid, kind, req)
        except ValueError: JOBS.pop(jid, None); raise
        return jid
    if path == '/api/job_cancel':
        j = JOBS.get(str(b.get('id', '')))
        if not j: raise ValueError('no such job')
        j['cancel'] = True; return 'cancelling'
    if path == '/api/grid_slots':
        if not isinstance(b.get('slots'), list) or len(b['slots']) > 6: raise ValueError('0-6 grid slots')
        slots = [GRID.validate_slot(x, i, strategy_ids=[s_['id'] for s_ in e.S['SLEEVES']]) for i, x in enumerate(b['slots'])]
        if len({x['id'] for x in slots}) != len(slots): raise ValueError('each grid slot needs a unique name')
        used = sum(x['share'] for x in e.S['SLEEVES'] if x['enabled']) + sum(x['share'] for x in slots if x['enabled'])
        if used > 1.0001: raise ValueError('capital shares of strategies + grids add up to more than 100%')
        with e.lock: e.S['GRID_SLOTS'] = slots; e.save_settings()
        return 'grid slots saved'
    if path == '/api/grid_start':
        with e.lock: return e.grids.start(str(b.get('slot', '')), str(b.get('symbol', '')))
    if path == '/api/grid_stop':
        with e.lock: return e.grids.stop(str(b.get('slot', '')), str(b.get('symbol', '')), 'manual')
    if path == '/api/grid_preview':
        sym = str(b.get('symbol') or 'BTCUSDT')
        if not SYM.match(sym): raise ValueError('bad symbol')
        df = e.candles(sym, (b.get('cfg') or {}).get('tf', '4h') if (b.get('cfg') or {}).get('tf') in TF_SEC else '4h')
        px, atr = float(df.c.iloc[-1]), float(df.atr.iloc[-1])
        cap = (e.last_eq or 0) * _num((b.get('cfg') or {}).get('share', 0.25), 0.01, 1, 'share') / max(1, int(_num((b.get('cfg') or {}).get('max_coins', 4), 1, 20, 'coins')))
        cfg, m = GRID.validate_cfg(b.get('cfg') or {}, (b.get('cfg') or {}).get('max_worst_loss_pct'), price=px, atr_pct=atr / px, capital=cap or 100.0)
        return dict(cfg=cfg, metrics=m, price=px, atr_pct=atr / px, capital=cap)
    if path == '/api/backtest_delete':
        bid = str(b.get('id', ''))
        if not BT_ID.match(bid): raise ValueError('bad backtest id')
        f = os.path.join(DATA, 'backtests', f'{bid}.json')
        if os.path.realpath(os.path.dirname(f)) != os.path.realpath(os.path.join(DATA, 'backtests')): raise ValueError('bad backtest id')
        if os.path.exists(f): os.remove(f)
        return 'deleted'
    if path == '/api/action':
        a = b.get('action')
        if a == 'run_cycle': e.run_now.set(); return 'cycle requested'
        if a == 'refresh_signals': APP.preview(); return 'refreshing signals'
        if a == 'flatten':
            r = e.flatten()
            msg = f"closed {len(r['closed'])} position(s), entries paused"
            if r['failed']: raise ValueError(msg + f" - {len(r['failed'])} FAILED (their stops are still on Binance): " + '; '.join(f'{k.split("|")[1]}: {x}' for k, x in r['failed'])[:300])
            if r['still_open']: msg += ' - Binance still shows: ' + ', '.join(f'{k} {v}' for k, v in r['still_open'].items())
            return msg
        if a == 'close':
            with e.lock:
                if b.get('key') not in e.state['lots']: raise ValueError('that position is no longer open')
                e.close_lot(b['key'], 'closed_from_panel', (e.marks or {}).get(e.state['lots'][b['key']]['symbol']))
            return 'closed'
        if a == 'telegram_test':
            tok = e.cfg.get('TELEGRAM_TOKEN')
            if not (tok and e.S.get('TELEGRAM_CHAT')): raise ValueError('add the bot token and chat id first')
            import requests
            try:
                r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=10,
                                  data=dict(chat_id=e.S['TELEGRAM_CHAT'], text='ZackBot test message - notifications are working'))
            except Exception as ex:
                raise ValueError(f'could not reach Telegram ({type(ex).__name__})')
            if not r.ok: raise ValueError(f'Telegram said: {r.text[:150]}')
            return 'test message sent'
        if a == 'take_signal':
            e.take_signal(str(b.get('sleeve', '')), str(b.get('symbol', '')))
            return 'trade opened'
        if a == 'capital': return e.capital_action(b.get('kind'), b.get('amount'), str(b.get('note', ''))[:80])
        if a == 'move_stop': e.move_stop(b['key'], _num(b['stop'], 0, 1e12, 'stop')); return 'stop moved'
        if a == 'manual_trade':
            if b.get('side') not in ('LONG', 'SHORT'): raise ValueError('direction must be LONG or SHORT')
            ok = e.manual_trade(str(b.get('symbol', '')), b['side'], _num(b['risk'], 0.01, 100, 'risk') / 100, _num(b['stop_atr'], 0.1, 50, 'stop distance'),
                                _num(b['tp_r'], 0.5, 50, 'take profit') if b.get('tp_r') else None)
            if not ok: raise ValueError(e.last_skip or 'not opened')
            return 'manual trade opened'
        if a == 'test_connection': return APP.test_connection()
        if a == 'top_by_volume':
            t = Futures('', '', MAINNET).tickers_24h()
            ok = [x for x in t if x['symbol'].endswith('USDT') and SYM.match(x['symbol']) and (not e.rules or x['symbol'] in e.rules)]
            return [x['symbol'] for x in sorted(ok, key=lambda x: -float(x['quoteVolume']))[:int(_num(b.get('n', 40), 5, 100, 'n'))]]
        if a == 'quit':
            log.info('quit from panel (exchange stops stay active)')
            e.notify('⏹ ZackBot was closed from the app. Exchange stops stay active, but nothing manages trades until it runs again.')
            with e.lock: e.save_state()
            threading.Timer(1.0, lambda: os._exit(0)).start(); return 'bye'
    raise ValueError('unknown request')


# ------------------------------------------------------------------ window
SESSION_F = os.path.join(DATA, 'session.json')


def find_browser():
    cands = [os.path.join(os.environ.get(v, ''), *p) for v in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA')
             for p in (('Microsoft', 'Edge', 'Application', 'msedge.exe'), ('Google', 'Chrome', 'Application', 'chrome.exe'))]
    return next((c for c in cands if os.path.exists(c)), None)


def open_window(token):
    b = find_browser()
    url = f'http://127.0.0.1:{PORT}/?t={token}'
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


def message_box(text):
    log.error(text)
    if os.name == 'nt':
        try:
            import ctypes; ctypes.windll.user32.MessageBoxW(None, text, 'ZackBot', 0x30)
        except Exception: pass


def existing_instance_token():
    """Port already taken: only open it if it is really ZackBot (it must accept the token we saved at its launch)."""
    try:
        tok = json.load(open(SESSION_F)).get('token', '')
        import urllib.request
        nonce = secrets.token_hex(16)
        r = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/ping?nonce={nonce}', timeout=4).read())
        want = hmac.new(tok.encode(), nonce.encode(), 'sha256').hexdigest()
        if tok and r.get('app') == 'zackbot' and hmac.compare_digest(str(r.get('proof', '')), want): return tok
    except Exception:
        pass
    return None


def _safe(f):
    try: return f()
    except Exception as ex:
        log.debug(f'view helper: {ex}'); return None


def selftest(path):
    """build_app.bat runs the new exe with --selftest <file> BEFORE replacing the old one: proves the bundle imports, has its
    data files and time-zone data, and reports the version/build id it was built from."""
    res = dict(version=VERSION, build=BUILD_ID, ok=False)
    try:
        import zoneinfo
        zoneinfo.ZoneInfo('Africa/Cairo')
        for f in ('panel.html', 'research', os.path.join('data', 'exchange_rules_testnet.json')):
            if not os.path.exists(os.path.join(BUNDLE, f)): raise RuntimeError(f'{f} missing from the bundle')
        import lab, grid, telegram_ctl, ai_filter    # noqa: F401  (every module the app loads lazily)
        if not PRESETS or not S.STRATEGIES: raise RuntimeError('presets/strategies missing')
        res['ok'] = True
    except Exception as e:
        res['error'] = f'{type(e).__name__}: {e}'
    with open(path, 'w', encoding='utf-8') as f: json.dump(res, f)


def main():
    global APP
    if '--selftest' in sys.argv:
        i = sys.argv.index('--selftest')
        selftest(sys.argv[i + 1] if i + 1 < len(sys.argv) else os.path.join(DATA, 'selftest.json')); return
    if '--simulate-failed-launch' in sys.argv:   # installer rollback drill ONLY (build_app.bat drill): fail before anything
        log.warning('simulate-failed-launch: exiting before the panel/engine start (installer rollback drill)')   # starts:
        sys.exit(3)                              # no port, no session.json, no engine, no exchange call
    if port_in_use():                       # already running -> just show it (after verifying it is ours)
        tok = existing_instance_token()
        if tok: open_window(tok)
        else: message_box(f'Port {PORT} is used by another program, not ZackBot. Close that program and start ZackBot again.')
        return
    save_json(SESSION_F, dict(token=TOKEN, port=PORT, pid=os.getpid(), started=datetime.now(timezone.utc).isoformat(timespec='seconds')))
    threading.Thread(target=job_worker, daemon=True).start()
    APP = App()
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=APP.loop, daemon=True).start()
    log.info(f'control panel at http://127.0.0.1:{PORT} (opens from the ZackBot shortcut)')
    APP.engine.notify(f'▶️ ZackBot {VERSION} started ({"LIVE" if APP.engine.live else "paper"}).')
    if '--no-window' in sys.argv:
        while True: time.sleep(3600)
    w = open_window(TOKEN)
    while True:
        time.sleep(2)
        if w is not None and w.poll() is not None:
            if APP.engine.S.get('RUN_IN_BACKGROUND', True):
                log.info('window closed - bot keeps running in the background (open ZackBot again to see it)')
                w = None
            else:
                log.info('window closed - quitting (exchange stops stay active)')
                with APP.engine.lock: APP.engine.save_state()
                os._exit(0)


if __name__ == '__main__':
    main()
