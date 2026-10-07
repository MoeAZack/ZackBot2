"""Market-data collector core (observe-only): public Binance USD-M futures market data -> CSV files + a manifest.

One implementation shared by the standalone tool (tools/collect_market_data.py, plain `requests`) and the in-app
background collector (market_collector.py, the engine's public MAINNET data client). Public endpoints only: no API key
is ever sent, nothing here can place, change or read orders.

Datasets (one CSV per dataset / symbol / period under <out>/<dataset>/):
  open_interest_hist   /futures/data/openInterestHist             1h, 4h   last 30 days only (Binance limit)
  global_ls_account    /futures/data/globalLongShortAccountRatio  1h, 4h   last 30 days only
  top_ls_position      /futures/data/topLongShortPositionRatio    1h, 4h   last 30 days only
  taker_ls_ratio       /futures/data/takerlongshortRatio          1h, 4h   last 30 days only
  funding_rate         /fapi/v1/fundingRate                       full history from the symbol's onboard date
  mark_klines          /fapi/v1/markPriceKlines                   1h, 4h   full history (closed candles only)
  funding_info         /fapi/v1/fundingInfo                       change log of funding interval / cap / floor
  exchange_info        /fapi/v1/exchangeInfo                      raw JSON snapshot (new file only when it changed)

Every run back-fills what is missing and appends what is new; rows are de-duplicated by their timestamp, so running
twice (or after a crash) never duplicates anything. Each file is rewritten atomically (temp file + os.replace): a
crash mid-write leaves the previous complete file. All file I/O is UTF-8 (Windows CP1252 safe).
"""
import csv, hashlib, json, os, random, time
from datetime import datetime, timezone

MAINNET = 'https://fapi.binance.com'
TESTNET = 'https://testnet.binancefuture.com'
CORE8 = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT']

HOUR_MS = 3600 * 1000
DAY_MS = 24 * HOUR_MS
PERIOD_MS = {'1h': HOUR_MS, '4h': 4 * HOUR_MS}
WINDOW_MS = 30 * DAY_MS - HOUR_MS            # Binance serves ~30 days of /futures/data history; keep a 1h safety margin
DEFAULT_ONBOARD_MS = 1567296000000           # 2019-09-01: first USD-M perpetual (used when exchangeInfo has no onboardDate)
MAX_PAGES = 400                              # hard bound per series and run (full 1h history of BTC is ~45 pages)
BAN_FILE = 'ban.json'
LOCK_FILE = '.collector.lock'
LOCK_STALE_S = 6 * 3600

# name -> spec. kind: 'window' (30-day /futures/data), 'forward' (paginate forward from the last row), 'snapshot'
DATASETS = {
    'open_interest_hist': dict(kind='window', path='/futures/data/openInterestHist', limit=500, ts='timestamp', periods=('1h', '4h'),
                               cols=['timestamp', 'sumOpenInterest', 'sumOpenInterestValue', 'CMCirculatingSupply']),
    'global_ls_account': dict(kind='window', path='/futures/data/globalLongShortAccountRatio', limit=500, ts='timestamp', periods=('1h', '4h'),
                              cols=['timestamp', 'longShortRatio', 'longAccount', 'shortAccount']),
    'top_ls_position': dict(kind='window', path='/futures/data/topLongShortPositionRatio', limit=500, ts='timestamp', periods=('1h', '4h'),
                            cols=['timestamp', 'longShortRatio', 'longAccount', 'shortAccount']),
    'taker_ls_ratio': dict(kind='window', path='/futures/data/takerlongshortRatio', limit=500, ts='timestamp', periods=('1h', '4h'),
                           cols=['timestamp', 'buySellRatio', 'buyVol', 'sellVol']),
    'funding_rate': dict(kind='forward', path='/fapi/v1/fundingRate', limit=1000, ts='fundingTime', periods=(None,),
                         cols=['fundingTime', 'fundingRate', 'markPrice']),
    'mark_klines': dict(kind='forward', path='/fapi/v1/markPriceKlines', limit=1500, ts='open_time', periods=('1h', '4h'),
                        cols=['open_time', 'open', 'high', 'low', 'close', 'close_time']),
}
SERIES_DATASETS = tuple(DATASETS)
# Per-endpoint pacing (seconds between two requests): fundingRate + fundingInfo share 500 req / 5 min per IP; the
# /futures/data endpoints allow 1000 req / 5 min; klines with limit 1500 cost weight 10 of the 2400 / min IP budget.
ENDPOINT_WEIGHT = {'/fapi/v1/markPriceKlines': 10, '/fapi/v1/exchangeInfo': 1, '/fapi/v1/fundingRate': 1, '/fapi/v1/fundingInfo': 1}
ENDPOINT_MIN_GAP = {'/fapi/v1/fundingRate': 0.7, '/fapi/v1/fundingInfo': 0.7, '/futures/data/': 0.35}


# ------------------------------------------------------------------ errors raised by transports
class RateLimited(Exception):
    """HTTP 429 (or code -1003 without a ban): back off and retry, honouring Retry-After."""
    def __init__(self, retry_after=0.0, msg=''):
        super().__init__(msg or f'rate limited (retry after {retry_after}s)'); self.retry_after = float(retry_after or 0)


class Banned(Exception):
    """HTTP 418: the IP is banned. The run stops at once; nothing is sent until the ban has passed."""
    def __init__(self, retry_after=0.0, msg=''):
        super().__init__(msg or f'IP banned (HTTP 418, retry after {retry_after}s)'); self.retry_after = float(retry_after or 0)


class Transient(Exception):
    """Network error / 5xx / timeout: bounded retries with back-off."""


class Refused(Exception):
    """A 4xx business refusal (unknown symbol, start time outside the window, ...): this series is skipped."""
    def __init__(self, code=None, msg=''):
        super().__init__(f'{code}: {msg}'); self.code = code


class StopRun(Exception):
    """Abort the whole run (ban, rate limit that does not clear, or the caller's circuit says Binance is down)."""


# ------------------------------------------------------------------ helpers
def utc_iso(ms):
    if ms in (None, ''): return ''
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def now_ms(clock=time.time):
    return int(clock() * 1000)


def _retry_after(headers):
    try:
        v = float((headers or {}).get('Retry-After') or 0)
        return min(3600.0, max(0.0, v)) if v == v else 0.0
    except (TypeError, ValueError):
        return 0.0


def atomic_write_text(path, text):
    """Write the whole file to <path>.tmp then os.replace: readers only ever see the old or the new complete file."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = f'{path}.{os.getpid()}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.write(text); f.flush()
            try: os.fsync(f.fileno())
            except OSError: pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + '\n')


def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f: return json.load(f)
    except (OSError, ValueError):
        return default


def series_path(out, dataset, symbol, period):
    name = f'{symbol}_{period}.csv' if period else f'{symbol}.csv'
    return os.path.join(out, dataset, name)


def series_key(dataset, symbol, period):
    return f'{dataset}/{symbol}/{period}' if period else f'{dataset}/{symbol}'


def read_rows(path, ts_col):
    """{ts(int): row dict} from an existing series CSV (missing / unreadable file -> empty; bad rows are dropped)."""
    out = {}
    try:
        with open(path, encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f):
                try: out[int(row[ts_col])] = row
                except (KeyError, TypeError, ValueError): continue
    except OSError:
        pass
    return out


def write_rows(path, cols, rows):
    """rows: {ts: row} -> sorted CSV with a readable time_utc column, written atomically."""
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols + ['time_utc'], extrasaction='ignore', lineterminator='\n')
    w.writeheader()
    for ts in sorted(rows):
        r = dict(rows[ts]); r['time_utc'] = utc_iso(ts)
        w.writerow(r)
    atomic_write_text(path, buf.getvalue())


def normalise(dataset, raw):
    """API answer -> list of row dicts keyed by the dataset's columns."""
    spec = DATASETS[dataset]
    rows = []
    for x in raw or []:
        if dataset == 'mark_klines':
            if not isinstance(x, (list, tuple)) or len(x) < 7: continue
            rows.append(dict(open_time=int(x[0]), open=x[1], high=x[2], low=x[3], close=x[4], close_time=int(x[6])))
        elif isinstance(x, dict) and spec['ts'] in x:
            r = {c: x.get(c, '') for c in spec['cols']}
            r[spec['ts']] = int(x[spec['ts']])
            rows.append(r)
    return rows


# ------------------------------------------------------------------ transports
class RequestsTransport:
    """Plain `requests` transport for the standalone tool. Never sends an API key."""
    def __init__(self, base=MAINNET, session=None, timeout=20):
        import requests
        self.base, self.timeout, self.requests = base.rstrip('/'), timeout, requests
        self.s = session or requests.Session()
        self.s.headers.pop('X-MBX-APIKEY', None)
        self.used_weight = None

    def get(self, path, params):
        try:
            r = self.s.get(self.base + path, params=params, timeout=self.timeout)
        except self.requests.RequestException as ex:
            raise Transient(f'{type(ex).__name__}') from None
        try: self.used_weight = int(r.headers.get('X-MBX-USED-WEIGHT-1M'))
        except (TypeError, ValueError): self.used_weight = None
        if r.status_code == 418: raise Banned(_retry_after(r.headers), f'HTTP 418 on {path}')
        if r.status_code == 429: raise RateLimited(_retry_after(r.headers), f'HTTP 429 on {path}')
        try: data = r.json() if r.content else None
        except ValueError: data = None
        if r.status_code >= 500 or (data is None and r.status_code != 200): raise Transient(f'HTTP {r.status_code} on {path}')
        if isinstance(data, dict) and data.get('code') not in (None, 0, 200):
            if data.get('code') == -1003: raise RateLimited(_retry_after(r.headers), str(data.get('msg'))[:160])
            if r.status_code >= 400 or isinstance(data.get('code'), int): raise Refused(data.get('code'), str(data.get('msg'))[:160])
        if r.status_code >= 400: raise Refused(-r.status_code, f'HTTP {r.status_code} on {path}')
        return data


# ------------------------------------------------------------------ the collector
class Collector:
    """One collection run over a universe. `transport.get(path, params)` returns the decoded JSON or raises one of the
    errors above. `sleep` / `clock` are injectable (tests). `gate()` (optional) is checked before every request: a
    non-empty string aborts the run (the in-app collector uses it for the engine's outage circuit / off switch)."""

    def __init__(self, transport, out, source=MAINNET, sleep=time.sleep, clock=time.time, weight_per_min=1200, min_gap=0.25,
                 max_retries=4, gate=None, log=None, max_run_s=None):
        self.t, self.out, self.source = transport, out, source
        self.sleep, self.clock, self.weight_per_min, self.min_gap = sleep, clock, float(weight_per_min), float(min_gap)
        self.max_retries, self.gate, self.log, self.max_run_s = int(max_retries), gate, log, max_run_s
        self._last = {}                     # endpoint -> time of the last request
        self._last_any = 0.0
        self.requests = 0
        self.rows_added = 0
        self.errors = []                    # [str] (bounded)
        self.gaps = []
        self.t0 = None
        self.manifest = read_json(os.path.join(out, 'manifest.json'), {}) or {}
        self.manifest.setdefault('series', {})

    # -------------------------------------------------------------- request with pacing / back-off
    def _pace(self, path):
        """Sleep so that: any two requests are >= min_gap apart, and requests to the same endpoint respect its weight
        share of `weight_per_min` and its own per-endpoint minimum gap."""
        gap = max(self.min_gap, 60.0 * ENDPOINT_WEIGHT.get(path, 1) / self.weight_per_min)
        for prefix, g in ENDPOINT_MIN_GAP.items():
            if path.startswith(prefix): gap = max(gap, g)
        now = self.clock()
        wait = max(self._last.get(path, -1e18) + gap, self._last_any + self.min_gap) - now
        uw = getattr(self.t, 'used_weight', None)
        if isinstance(uw, int) and uw > 1800:              # close to the 2400 / min IP budget: wait for the next minute
            wait = max(wait, 61 - (now % 60))
        if wait > 0: self.sleep(wait)
        self._last[path] = self._last_any = self.clock()

    def get(self, path, params):
        for attempt in range(self.max_retries + 1):
            if self.gate is not None:
                why = self.gate()
                if why: raise StopRun(why)
            if self.max_run_s and self.t0 is not None and self.clock() - self.t0 > self.max_run_s:
                raise StopRun(f'run time limit {int(self.max_run_s)}s reached (continues next run)')
            self._pace(path)
            self.requests += 1
            try:
                return self.t.get(path, params)
            except Banned as b:
                until = self.clock() + max(b.retry_after, 120.0)
                atomic_write_json(os.path.join(self.out, BAN_FILE), dict(until=until, until_utc=utc_iso(until * 1000), why=str(b)[:200]))
                raise StopRun(f'{b} - collection stopped until {utc_iso(until * 1000)}') from None
            except RateLimited as rl:
                if attempt >= self.max_retries: raise StopRun(f'rate limit did not clear after {attempt + 1} tries: {rl}') from None
                self.sleep(min(300.0, rl.retry_after or 2.0 * 2 ** attempt))
            except Transient as tr:
                if attempt >= self.max_retries: raise
                self.sleep(min(60.0, 1.0 * 2 ** attempt + random.random()))
        raise Transient('retries exhausted')

    def _err(self, msg):
        self.errors.append(str(msg)[:200]); del self.errors[:-50]
        if self.log: self.log(f'market data: {msg}')

    # -------------------------------------------------------------- series
    def _store(self, dataset, symbol, period, existing, new_rows):
        """Merge new rows over the existing ones (same timestamp -> the newer answer wins), write atomically, update the
        manifest entry. Returns the number of NEW timestamps."""
        spec = DATASETS[dataset]
        added = 0
        for r in new_rows:
            ts = int(r[spec['ts']])
            if ts not in existing: added += 1
            existing[ts] = r
        path = series_path(self.out, dataset, symbol, period)
        if added or not os.path.exists(path) and existing:
            write_rows(path, spec['cols'], existing)
        self.rows_added += added
        key = series_key(dataset, symbol, period)
        e = self.manifest['series'].get(key, {})
        e.update(dataset=dataset, symbol=symbol, period=period, rows=len(existing), file=os.path.relpath(path, self.out).replace(os.sep, '/'),
                 first_ts=min(existing) if existing else None, last_ts=max(existing) if existing else None,
                 first_utc=utc_iso(min(existing)) if existing else '', last_utc=utc_iso(max(existing)) if existing else '',
                 last_run=utc_iso(now_ms(self.clock)), source=self.source)
        self.manifest['series'][key] = e
        return added

    def collect_window(self, dataset, symbol, period):
        """30-day /futures/data series: request [start, end] in chunks of at most `limit` periods each, so the answer
        never depends on how Binance orders rows when both startTime and endTime are given."""
        spec = DATASETS[dataset]
        pms, lim = PERIOD_MS[period], spec['limit']
        path = series_path(self.out, dataset, symbol, period)
        existing = read_rows(path, spec['ts'])
        now = now_ms(self.clock)
        window_start = -(-(now - WINDOW_MS) // pms) * pms                 # first period boundary inside the window
        start = window_start
        if existing:
            last = max(existing)
            if last + pms < window_start:
                gap = f'{series_key(dataset, symbol, period)}: gap {utc_iso(last)} -> {utc_iso(window_start)} (older data is no longer served)'
                self.gaps.append(gap); self._err(gap)
                self.manifest['series'].setdefault(series_key(dataset, symbol, period), {}).setdefault('gaps', []).append(
                    dict(after=utc_iso(last), before=utc_iso(window_start)))
            start = max(window_start, last + pms)
        new, pages = [], 0
        while start <= now and pages < MAX_PAGES:
            end = min(now, start + (lim - 1) * pms)
            raw = self.get(spec['path'], dict(symbol=symbol, period=period, limit=lim, startTime=start, endTime=end))
            pages += 1
            new += [r for r in normalise(dataset, raw) if start <= int(r[spec['ts']]) <= end]
            start = end + 1
        return self._store(dataset, symbol, period, existing, new)

    def collect_forward(self, dataset, symbol, period, onboard_ms):
        """Full-history series: page forward from the last stored row (or the onboard date) with startTime only.
        Partial progress is checkpointed every 20 pages, so a stopped run resumes where it ended."""
        spec = DATASETS[dataset]
        lim, tsc = spec['limit'], spec['ts']
        path = series_path(self.out, dataset, symbol, period)
        existing = read_rows(path, tsc)
        step = PERIOD_MS[period] if period else 1
        start = (max(existing) + step) if existing else int(onboard_ms or DEFAULT_ONBOARD_MS)
        added, buf, pages = 0, [], 0
        try:
            while pages < MAX_PAGES:
                now = now_ms(self.clock)
                if start > now: break
                params = dict(symbol=symbol, limit=lim, startTime=start)
                if period: params['interval'] = period
                raw = self.get(spec['path'], params)
                pages += 1
                rows = normalise(dataset, raw)
                if dataset == 'mark_klines': rows = [r for r in rows if int(r['close_time']) < now]      # closed candles only
                rows = [r for r in rows if int(r[tsc]) >= start]
                if not rows: break
                buf += rows
                start = max(int(r[tsc]) for r in rows) + step
                if pages % 20 == 0:
                    added += self._store(dataset, symbol, period, existing, buf); buf = []
                if len(raw or []) < lim: break
        finally:
            if buf or not existing: added += self._store(dataset, symbol, period, existing, buf)
        return added

    # -------------------------------------------------------------- snapshots
    def snapshot_exchange_info(self, env='mainnet'):
        """Raw exchangeInfo JSON. <out>/exchange_info/<env>_exchangeInfo_latest.json is always the newest answer (the
        input format of exchange_rules.py `build`); a timestamped copy is kept only when the content changed."""
        info = self.get('/fapi/v1/exchangeInfo', {})
        if not isinstance(info, dict) or not isinstance(info.get('symbols'), list): raise Transient('exchangeInfo answer has no symbols')
        fetched = now_ms(self.clock)
        d = os.path.join(self.out, 'exchange_info')
        body = {k: v for k, v in info.items() if k != 'serverTime'}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode('utf-8')).hexdigest()
        latest = os.path.join(d, f'{env}_exchangeInfo_latest.json')
        key = f'exchange_info/{env}'
        prev = self.manifest['series'].get(key, {})
        stamped = prev.get('stamped_file')
        if prev.get('sha256') != digest or not stamped or not os.path.exists(os.path.join(self.out, stamped)):
            name = f"{env}_exchangeInfo_{datetime.fromtimestamp(fetched / 1000, timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
            atomic_write_json(os.path.join(d, name), info)
            stamped = f'exchange_info/{name}'
        atomic_write_json(latest, info)
        self.manifest['series'][key] = dict(dataset='exchange_info', env=env, symbols=len(info['symbols']), sha256=digest,
                                            fetched_ms=fetched, fetched_utc=utc_iso(fetched), stamped_file=stamped,
                                            file=f'exchange_info/{env}_exchangeInfo_latest.json', last_run=utc_iso(fetched),
                                            source=self.source)
        return info, latest, fetched

    def snapshot_funding_info(self):
        """fundingInfo change log: a row per (symbol, interval, cap, floor) the first time it is seen."""
        raw = self.get('/fapi/v1/fundingInfo', {}) or []
        path = os.path.join(self.out, 'funding_info', 'ALL.csv')
        cols = ['symbol', 'fundingIntervalHours', 'adjustedFundingRateCap', 'adjustedFundingRateFloor', 'first_seen_utc']
        seen, rows = set(), []
        try:
            with open(path, encoding='utf-8', newline='') as f:
                for r in csv.DictReader(f):
                    rows.append(r); seen.add(tuple(r.get(c, '') for c in cols[:4]))
        except OSError:
            pass
        stamp, added = utc_iso(now_ms(self.clock)), 0
        for x in raw if isinstance(raw, list) else []:
            if not isinstance(x, dict) or 'symbol' not in x: continue
            k = tuple(str(x.get(c, '')) for c in cols[:4])
            if k in seen: continue
            seen.add(k); added += 1
            rows.append(dict(zip(cols, k + (stamp,))))
        if added or not os.path.exists(path):
            import io
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction='ignore', lineterminator='\n')
            w.writeheader(); w.writerows(rows)
            atomic_write_text(path, buf.getvalue())
        self.rows_added += added
        self.manifest['series']['funding_info'] = dict(dataset='funding_info', rows=len(rows), file='funding_info/ALL.csv',
                                                       last_run=stamp, source=self.source)
        return added

    def sweep_tmp(self, older_s=3600):
        """Remove temp files a killed process left behind (<file>.<pid>.tmp older than an hour). Never touches data files."""
        cutoff = time.time() - older_s
        for dp, _dns, fns in os.walk(self.out):
            for fn in fns:
                parts = fn.rsplit('.', 2)
                if len(parts) == 3 and parts[2] == 'tmp' and parts[1].isdigit():
                    p = os.path.join(dp, fn)
                    try:
                        if os.path.getmtime(p) < cutoff: os.remove(p)
                    except OSError:
                        pass

    # -------------------------------------------------------------- one run
    def run(self, symbols, datasets=SERIES_DATASETS, periods=None, exchange_info=True, funding_info=True, run_kind='once'):
        """Back-fill + append everything for `symbols`. Returns a summary dict (also stored in the manifest)."""
        self.t0 = self.clock()
        started = utc_iso(now_ms(self.clock))
        summary = dict(started=started, symbols=len(symbols), source=self.source, kind=run_kind)
        stopped = None
        ban = read_json(os.path.join(self.out, BAN_FILE)) or {}
        self.sweep_tmp()
        try:
            if float(ban.get('until') or 0) > self.clock():
                raise StopRun(f"IP ban recorded until {ban.get('until_utc')} - nothing sent")
            onboard = {}
            if exchange_info:
                try:
                    info, _, _ = self.snapshot_exchange_info('mainnet' if self.source == MAINNET else 'testnet')
                    onboard = {s['symbol']: s.get('onboardDate') for s in info['symbols'] if isinstance(s, dict) and 'symbol' in s}
                except (Transient, Refused) as ex:
                    self._err(f'exchangeInfo: {ex}')
            if funding_info:
                try: self.snapshot_funding_info()
                except (Transient, Refused) as ex: self._err(f'fundingInfo: {ex}')
            # Priority: the 30-day series first (Binance stops serving them), then funding, then the long kline back-fill.
            todo = [(ds, sym, per) for ds in datasets for sym in symbols for per in DATASETS[ds]['periods']
                    if not (periods and per and per not in periods)]
            todo.sort(key=lambda x: (DATASETS[x[0]]['kind'] != 'window', x[0] == 'mark_klines'))     # stable sort
            skipped, fails = set(), 0
            for ds, sym, per in todo:
                if onboard and sym not in onboard:
                    if sym not in skipped: skipped.add(sym); self._err(f'{sym}: not in exchangeInfo (delisted or misspelt) - skipped')
                    continue
                try:
                    if DATASETS[ds]['kind'] == 'window': self.collect_window(ds, sym, per)
                    else: self.collect_forward(ds, sym, per, onboard.get(sym))
                    fails = 0
                except Refused as ex:
                    self._err(f'{series_key(ds, sym, per)}: refused {ex}')
                except Transient as ex:
                    self._err(f'{series_key(ds, sym, per)}: unreachable {ex}')
                    fails += 1
                    if fails >= 3: raise StopRun('Binance unreachable for 3 series in a row') from None
        except StopRun as ex:
            stopped = str(ex)[:200]
            self._err(f'run stopped: {stopped}')
        finally:
            summary.update(finished=utc_iso(now_ms(self.clock)), seconds=round(self.clock() - self.t0, 1), requests=self.requests,
                           rows_added=self.rows_added, errors=len(self.errors), last_errors=self.errors[-5:], stopped=stopped,
                           gaps=self.gaps[-10:])
            self.manifest['last_run'] = summary
            self.manifest['out'] = os.path.abspath(self.out)
            try: atomic_write_json(os.path.join(self.out, 'manifest.json'), self.manifest)
            except OSError as ex: summary['manifest_error'] = str(ex)[:120]
        return summary


# ------------------------------------------------------------------ single-writer lock (standalone tool vs. in-app thread)
def acquire_lock(out, clock=time.time, stale_s=LOCK_STALE_S):
    """True if this process now owns <out>/.collector.lock. A lock older than `stale_s` (crashed run) is taken over."""
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, LOCK_FILE)
    for _ in range(2):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump(dict(pid=os.getpid(), t=clock()), f)
            return True
        except FileExistsError:
            info = read_json(p, {}) or {}
            try: age = clock() - float(info.get('t', 0))
            except (TypeError, ValueError): age = stale_s + 1
            if age <= stale_s: return False
            try: os.remove(p)
            except OSError: return False
    return False


def release_lock(out):
    try: os.remove(os.path.join(out, LOCK_FILE))
    except OSError: pass
