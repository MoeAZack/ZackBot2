"""Market-data collector (market_data.py core, tools/collect_market_data.py, market_collector.py in-app thread).
No network: every test drives a fake Binance through an injected transport and a fake clock."""
import csv, io, json, os, sys, tempfile, threading, time, types
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'tools'))
import market_data as MD
import market_collector as MC
import binance_client as BC

H = MD.HOUR_MS
NOW = 1791331200000                       # 2026-10-07T00:00:00Z
ONBOARD = NOW - 400 * MD.DAY_MS           # ~13 months of history -> several kline pages


class Clock:
    def __init__(self, t_ms=NOW): self.t = t_ms / 1000.0; self.slept = []
    def __call__(self): return self.t
    def sleep(self, s): self.slept.append(s); self.t += max(0.0, s)


class FakeBinance:
    """Answers like Binance for the endpoints the collector uses; `script` is a list of exceptions (or None) consumed one
    per request before answering; `calls` records (path, params)."""
    def __init__(self, clock, symbols=('BTCUSDT', 'ETHUSDT'), onboard=ONBOARD, funding_h=8):
        self.clock, self.symbols, self.onboard, self.funding_h = clock, list(symbols), onboard, funding_h
        self.calls, self.script, self.used_weight = [], [], None

    def now(self): return int(self.clock() * 1000)

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        if self.script:
            ex = self.script.pop(0)
            if ex is not None: raise ex
        now = self.now()
        if path == '/fapi/v1/exchangeInfo':
            return dict(serverTime=now, rateLimits=[], symbols=[dict(symbol=s, onboardDate=self.onboard, status='TRADING') for s in self.symbols])
        if path == '/fapi/v1/fundingInfo':
            return [dict(symbol='BTCUSDT', fundingIntervalHours=8, adjustedFundingRateCap='0.02', adjustedFundingRateFloor='-0.02')]
        sym = params['symbol']
        if sym not in self.symbols: raise MD.Refused(-1121, 'Invalid symbol.')
        if path.startswith('/futures/data/'):
            p = MD.PERIOD_MS[params['period']]
            if params['startTime'] < now - 30 * MD.DAY_MS: raise MD.Refused(-1130, 'startTime outside the 30-day window')
            lo, hi = params['startTime'], min(params['endTime'], now)
            ts = [t for t in range(-(-lo // p) * p, hi + 1, p) if t + p <= now]
            ts = ts[-params['limit']:]                       # Binance's ambiguous ordering: the LAST rows of a long range
            return [dict(symbol=sym, timestamp=t, sumOpenInterest='1.5', sumOpenInterestValue='2.5', longShortRatio='1.1',
                         longAccount='0.52', shortAccount='0.48', buySellRatio='0.9', buyVol='10', sellVol='11') for t in ts]
        if path == '/fapi/v1/fundingRate':
            step = self.funding_h * H
            first = self.onboard + (-(self.onboard - params['startTime']) // step) * step if params['startTime'] > self.onboard else self.onboard
            out, t = [], first
            while t <= now and len(out) < params['limit']:
                out.append(dict(symbol=sym, fundingTime=t, fundingRate='0.0001', markPrice='100.0')); t += step
            return out
        if path == '/fapi/v1/markPriceKlines':
            p = MD.PERIOD_MS[params['interval']]
            t = max(self.onboard, -(-params['startTime'] // p) * p)
            out = []
            while t <= now and len(out) < params['limit']:     # includes the still-open candle like Binance
                out.append([t, '1', '2', '0.5', '1.5', '0', t + p - 1, '0', 0, '0', '0', '0']); t += p
            return out
        raise AssertionError(path)


def mk(tmp, clock=None, symbols=('BTCUSDT',), **kw):
    clock = clock or Clock()
    fb = FakeBinance(clock, symbols=symbols)
    c = MD.Collector(fb, str(tmp), sleep=clock.sleep, clock=clock, **kw)
    return c, fb, clock


def read_csv(path):
    with open(path, encoding='utf-8', newline='') as f: return list(csv.DictReader(f))


# ------------------------------------------------------------------ pagination + idempotency
def test_full_history_pagination_is_complete_ordered_and_closed_only(tmp_path):
    c, fb, clk = mk(tmp_path)
    s = c.run(['BTCUSDT'], datasets=('mark_klines', 'funding_rate'), funding_info=False)
    assert s['stopped'] is None and s['errors'] == 0, s
    for per in ('1h', '4h'):
        rows = read_csv(MD.series_path(str(tmp_path), 'mark_klines', 'BTCUSDT', per))
        ts = [int(r['open_time']) for r in rows]
        p = MD.PERIOD_MS[per]
        assert ts[0] == ONBOARD and ts == list(range(ONBOARD, ts[-1] + 1, p)), 'gapless, ascending, from onboard'
        assert ts[-1] + p <= NOW and all(int(r['close_time']) < NOW for r in rows), 'the open candle is never stored'
        pages = [x for x in fb.calls if x[0] == '/fapi/v1/markPriceKlines' and x[1]['interval'] == per]
        assert len(pages) == -(-len(ts) // 1500) or len(pages) == -(-len(ts) // 1500) + 1
        assert all(x[1]['limit'] == 1500 for x in pages)
    fr = read_csv(MD.series_path(str(tmp_path), 'funding_rate', 'BTCUSDT', None))
    assert len(fr) == (NOW - ONBOARD) // (8 * H) + 1 and len({r['fundingTime'] for r in fr}) == len(fr)
    assert all(x[1]['limit'] == 1000 for x in fb.calls if x[0] == '/fapi/v1/fundingRate')


def test_second_run_adds_nothing_and_later_runs_only_append_new_rows(tmp_path):
    c, fb, clk = mk(tmp_path)
    c.run(['BTCUSDT'])
    files = {}
    for dp, _, fns in os.walk(tmp_path):
        for fn in fns:
            if fn.endswith('.csv'): files[os.path.join(dp, fn)] = open(os.path.join(dp, fn), encoding='utf-8').read()
    c2 = MD.Collector(fb, str(tmp_path), sleep=clk.sleep, clock=clk)
    s2 = c2.run(['BTCUSDT'])
    assert s2['rows_added'] == 0 and s2['errors'] == 0
    for p, txt in files.items(): assert open(p, encoding='utf-8').read() == txt, f'{p} rewritten differently'
    clk.t += 8 * 3600                                                  # 8 hours later
    fb.calls.clear()
    s3 = MD.Collector(fb, str(tmp_path), sleep=clk.sleep, clock=clk).run(['BTCUSDT'])
    assert s3['rows_added'] > 0 and s3['errors'] == 0
    k1h = [x for x in fb.calls if x[0] == '/fapi/v1/markPriceKlines' and x[1]['interval'] == '1h']
    assert len(k1h) == 1 and k1h[0][1]['startTime'] == NOW, 'resumes right after the last stored closed candle'
    rows = read_csv(MD.series_path(str(tmp_path), 'mark_klines', 'BTCUSDT', '1h'))
    ts = [int(r['open_time']) for r in rows]
    assert len(ts) == len(set(ts)) and ts == sorted(ts) and ts[-1] == NOW + 7 * H
    oi = read_csv(MD.series_path(str(tmp_path), 'open_interest_hist', 'BTCUSDT', '1h'))
    assert len({r['timestamp'] for r in oi}) == len(oi)


def test_merge_dedupes_by_timestamp_and_newer_answer_wins(tmp_path):
    c, fb, clk = mk(tmp_path)
    ex = {}
    c._store('funding_rate', 'BTCUSDT', None, ex, [dict(fundingTime=1, fundingRate='0.1', markPrice='1')])
    n = c._store('funding_rate', 'BTCUSDT', None, ex, [dict(fundingTime=1, fundingRate='0.2', markPrice='1'),
                                                      dict(fundingTime=2, fundingRate='0.3', markPrice='1')])
    assert n == 1
    rows = read_csv(MD.series_path(str(tmp_path), 'funding_rate', 'BTCUSDT', None))
    assert [(r['fundingTime'], r['fundingRate']) for r in rows] == [('1', '0.2'), ('2', '0.3')]


# ------------------------------------------------------------------ 30-day window
def test_window_series_stay_inside_30_days_in_chunks_of_at_most_limit(tmp_path):
    c, fb, clk = mk(tmp_path)
    c.run(['BTCUSDT'], datasets=('open_interest_hist', 'global_ls_account', 'top_ls_position', 'taker_ls_ratio'), exchange_info=False,
          funding_info=False)
    reqs = [x[1] for x in fb.calls if x[0].startswith('/futures/data/')]
    assert reqs and all(r['startTime'] >= NOW - 30 * MD.DAY_MS for r in reqs)
    for r in reqs:
        p = MD.PERIOD_MS[r['period']]
        assert r['limit'] == 500 and (r['endTime'] - r['startTime']) // p + 1 <= 500
    rows = read_csv(MD.series_path(str(tmp_path), 'open_interest_hist', 'BTCUSDT', '1h'))
    ts = [int(r['timestamp']) for r in rows]
    assert len(ts) >= 29 * 24 and ts == list(range(ts[0], ts[-1] + 1, H)), 'chunking kept every hour (none lost to limit)'
    assert not c.gaps


def test_window_gap_after_a_long_pause_is_recorded_not_hidden(tmp_path):
    c, fb, clk = mk(tmp_path)
    c.run(['BTCUSDT'], datasets=('open_interest_hist',), exchange_info=False, funding_info=False)
    clk.t += 40 * 86400
    c2 = MD.Collector(fb, str(tmp_path), sleep=clk.sleep, clock=clk)
    s = c2.run(['BTCUSDT'], datasets=('open_interest_hist',), exchange_info=False, funding_info=False)
    assert s['errors'] == 2 and len(s['gaps']) == 2 and 'no longer served' in s['gaps'][0]
    m = MD.read_json(os.path.join(tmp_path, 'manifest.json'))
    assert m['series']['open_interest_hist/BTCUSDT/1h']['gaps']


# ------------------------------------------------------------------ rate limits
def test_429_backs_off_with_retry_after_then_succeeds(tmp_path):
    c, fb, clk = mk(tmp_path)
    fb.script = [None, None, MD.RateLimited(7.0), MD.RateLimited(0)]    # exchangeInfo, fundingInfo, then two 429s
    s = c.run(['BTCUSDT'], datasets=('funding_rate',))
    assert s['stopped'] is None and s['errors'] == 0
    assert 7.0 in clk.slept and 4.0 in clk.slept, clk.slept            # Retry-After honoured; no header -> 2 * 2**1
    assert read_csv(MD.series_path(str(tmp_path), 'funding_rate', 'BTCUSDT', None))


def test_429_that_never_clears_stops_the_run_bounded(tmp_path):
    c, fb, clk = mk(tmp_path, max_retries=3)
    fb.script = [None, None] + [MD.RateLimited(1.0)] * 50
    s = c.run(['BTCUSDT'], datasets=('funding_rate', 'mark_klines'))
    assert s['stopped'] and 'rate limit' in s['stopped']
    assert len(fb.calls) == 2 + 4, 'bounded: 1 try + 3 retries, then nothing more'


def test_418_stops_immediately_and_blocks_later_runs_until_the_ban_ends(tmp_path):
    c, fb, clk = mk(tmp_path)
    fb.script = [None, None, MD.Banned(600.0)]
    s = c.run(['BTCUSDT'])
    assert s['stopped'] and '418' in s['stopped'] and len(fb.calls) == 3
    ban = MD.read_json(os.path.join(tmp_path, MD.BAN_FILE))
    assert ban['until'] == pytest.approx(clk() + 600, abs=1)
    n = len(fb.calls)
    s2 = MD.Collector(fb, str(tmp_path), sleep=clk.sleep, clock=clk).run(['BTCUSDT'])
    assert s2['stopped'] and len(fb.calls) == n, 'nothing is sent while banned'
    clk.t += 601
    s3 = MD.Collector(fb, str(tmp_path), sleep=clk.sleep, clock=clk).run(['BTCUSDT'], datasets=('funding_rate',))
    assert s3['stopped'] is None and len(fb.calls) > n


def test_requests_are_paced(tmp_path):
    c, fb, clk = mk(tmp_path, weight_per_min=600)
    times = []
    orig = fb.get
    fb.get = lambda p, q: (times.append((p, clk())), orig(p, q))[1]
    c.run(['BTCUSDT'], datasets=('mark_klines',), exchange_info=False, funding_info=False)
    kt = [t for p, t in times if p == '/fapi/v1/markPriceKlines']
    assert len(kt) > 2 and all(b - a >= 1.0 - 1e-6 for a, b in zip(kt, kt[1:])), 'weight 10 at 600/min -> >= 1 s apart'


def test_unreachable_binance_stops_after_three_series(tmp_path):
    c, fb, clk = mk(tmp_path, symbols=('BTCUSDT', 'ETHUSDT'), max_retries=1)
    fb.script = [None, None] + [MD.Transient('down')] * 100
    s = c.run(['BTCUSDT', 'ETHUSDT'])
    assert s['stopped'] and 'unreachable' in s['stopped'] and len(fb.calls) == 2 + 3 * 2


def test_refused_series_is_skipped_others_continue(tmp_path):
    c, fb, clk = mk(tmp_path, symbols=('BTCUSDT',))
    s = c.run(['BTCUSDT', 'NOPEUSDT'], datasets=('funding_rate',))
    assert s['errors'] == 1 and 'NOPEUSDT' in s['last_errors'][0]
    assert read_csv(MD.series_path(str(tmp_path), 'funding_rate', 'BTCUSDT', None))


# ------------------------------------------------------------------ manifest + snapshots
def test_manifest_tracks_rows_first_last_and_source(tmp_path):
    c, fb, clk = mk(tmp_path)
    c.run(['BTCUSDT'])
    m = MD.read_json(os.path.join(tmp_path, 'manifest.json'))
    e = m['series']['mark_klines/BTCUSDT/4h']
    rows = read_csv(os.path.join(tmp_path, e['file']))
    assert e['rows'] == len(rows) and e['first_ts'] == ONBOARD and e['last_ts'] == int(rows[-1]['open_time'])
    assert e['source'] == MD.MAINNET and e['last_run'].startswith('2026-10-07T')
    assert m['last_run']['rows_added'] > 0 and m['last_run']['kind'] == 'once'
    assert {'funding_info', 'exchange_info/mainnet', 'funding_rate/BTCUSDT', 'taker_ls_ratio/BTCUSDT/1h'} <= set(m['series'])


def test_exchange_info_snapshot_new_file_only_when_content_changes(tmp_path):
    c, fb, clk = mk(tmp_path)
    c.snapshot_exchange_info('mainnet')
    clk.t += 3600
    c.snapshot_exchange_info('mainnet')                     # same content (serverTime differs) -> no new stamped copy
    d = os.path.join(tmp_path, 'exchange_info')
    assert len([f for f in os.listdir(d) if 'latest' not in f]) == 1
    fb.symbols.append('SOLUSDT'); clk.t += 3600
    c.snapshot_exchange_info('mainnet')
    assert len([f for f in os.listdir(d) if 'latest' not in f]) == 2
    latest = MD.read_json(os.path.join(d, 'mainnet_exchangeInfo_latest.json'))
    assert len(latest['symbols']) == 2 and 'serverTime' in latest       # raw answer: exchange_rules.py `build` input


def test_funding_info_logs_changes_once(tmp_path):
    c, fb, clk = mk(tmp_path)
    assert c.snapshot_funding_info() == 1 and c.snapshot_funding_info() == 0
    rows = read_csv(os.path.join(tmp_path, 'funding_info', 'ALL.csv'))
    assert rows[0]['symbol'] == 'BTCUSDT' and rows[0]['fundingIntervalHours'] == '8'


# ------------------------------------------------------------------ atomic writes
def test_failed_replace_never_corrupts_the_existing_file(tmp_path, monkeypatch):
    p = os.path.join(tmp_path, 'x', 'f.csv')
    MD.atomic_write_text(p, 'old,complete\n')
    def boom(a, b): raise OSError('disk full')
    monkeypatch.setattr(MD.os, 'replace', boom)
    with pytest.raises(OSError): MD.atomic_write_text(p, 'new' * 1000)
    assert open(p, encoding='utf-8').read() == 'old,complete\n'
    assert os.listdir(os.path.dirname(p)) == ['f.csv'], 'no temp file left behind'


def test_crash_mid_write_keeps_the_previous_complete_csv(tmp_path, monkeypatch):
    c, fb, clk = mk(tmp_path)
    c.run(['BTCUSDT'], datasets=('funding_rate',), exchange_info=False, funding_info=False)
    p = MD.series_path(str(tmp_path), 'funding_rate', 'BTCUSDT', None)
    before = open(p, encoding='utf-8').read()
    real_open = open
    class Partial(io.StringIO):
        def __init__(self, path): super().__init__(); self.path = path
        def write(self, s):
            with real_open(self.path, 'w', encoding='utf-8') as f: f.write(s[:10])    # half the bytes reach the disk
            raise OSError('power cut')
        def fileno(self): return -1
    monkeypatch.setattr('builtins.open', lambda path, mode='r', *a, **k: Partial(path) if path.endswith('.tmp') and 'w' in mode
                        else real_open(path, mode, *a, **k))
    clk.t += 86400
    with pytest.raises(OSError):
        c._store('funding_rate', 'BTCUSDT', None, MD.read_rows(p, 'fundingTime'), [dict(fundingTime=NOW + 1, fundingRate='1', markPrice='1')])
    monkeypatch.undo()
    assert open(p, encoding='utf-8').read() == before
    assert not [f for f in os.listdir(os.path.dirname(p)) if f.endswith('.tmp')]


def test_interrupted_backfill_resumes_from_its_checkpoint(tmp_path):
    c, fb, clk = mk(tmp_path)
    fb.script = [None] * 3 + [MD.Banned(1.0)]                         # stop during the 1h kline back-fill (7 pages)
    s = c.run(['BTCUSDT'], datasets=('mark_klines',), exchange_info=False, funding_info=False, periods=('1h',))
    assert s['stopped']
    rows = read_csv(MD.series_path(str(tmp_path), 'mark_klines', 'BTCUSDT', '1h'))
    assert len(rows) == 3 * 1500, 'every fetched page before the stop was kept (checkpoint + final store)'
    clk.t += 300
    fb.calls.clear()
    MD.Collector(fb, str(tmp_path), sleep=clk.sleep, clock=clk).run(['BTCUSDT'], datasets=('mark_klines',), exchange_info=False,
                                                                     funding_info=False, periods=('1h',))
    assert fb.calls[0][1]['startTime'] == ONBOARD + 3 * 1500 * H
    ts = [int(r['open_time']) for r in read_csv(MD.series_path(str(tmp_path), 'mark_klines', 'BTCUSDT', '1h'))]
    assert ts == list(range(ONBOARD, ts[-1] + 1, H))


def test_temp_files_left_by_a_killed_process_are_swept(tmp_path):
    c, fb, clk = mk(tmp_path)
    d = os.path.join(tmp_path, 'mark_klines'); os.makedirs(d)
    old_tmp, new_tmp, data = (os.path.join(d, n) for n in ('BTCUSDT_1h.csv.4242.tmp', 'BTCUSDT_4h.csv.4243.tmp', 'keep.csv'))
    for p in (old_tmp, new_tmp, data): open(p, 'w', encoding='utf-8').write('x')
    os.utime(old_tmp, (time.time() - 7200, time.time() - 7200)); os.utime(data, (time.time() - 7200, time.time() - 7200))
    c.sweep_tmp()
    assert not os.path.exists(old_tmp) and os.path.exists(new_tmp) and os.path.exists(data)


def test_lock_is_single_writer_and_stale_lock_is_taken_over(tmp_path):
    clk = Clock()
    assert MD.acquire_lock(str(tmp_path), clock=clk) and not MD.acquire_lock(str(tmp_path), clock=clk)
    clk.t += MD.LOCK_STALE_S + 1
    assert MD.acquire_lock(str(tmp_path), clock=clk)
    MD.release_lock(str(tmp_path))
    assert not os.path.exists(os.path.join(tmp_path, MD.LOCK_FILE))


# ------------------------------------------------------------------ CP1252 / UTF-8
def test_every_file_open_names_utf8():
    import re
    for f in ('market_data.py', 'market_collector.py', os.path.join('tools', 'collect_market_data.py')):
        src = open(os.path.join(ROOT, f), encoding='utf-8').read()
        for m in re.finditer(r'(?<![\w.])open\(([^\n]*)', src):
            assert "encoding='utf-8'" in m.group(1), f'{f}: {m.group(0)}'
        assert 'fdopen' not in src or "fdopen(fd, 'w', encoding='utf-8')" in src


def test_non_ascii_survives_a_round_trip(tmp_path):
    c, fb, clk = mk(tmp_path)
    ex = {}
    c._store('funding_rate', 'BTCUSDT', None, ex, [dict(fundingTime=5, fundingRate='0.1', markPrice='€1→2')])
    rows = read_csv(MD.series_path(str(tmp_path), 'funding_rate', 'BTCUSDT', None))
    assert rows[0]['markPrice'] == '€1→2'
    MD.atomic_write_json(os.path.join(tmp_path, 'j.json'), dict(x='é→'))
    assert MD.read_json(os.path.join(tmp_path, 'j.json')) == dict(x='é→')


# ------------------------------------------------------------------ standalone tool
def test_cli_once_symbols_out_and_exit_codes(tmp_path):
    import collect_market_data as T
    clk = Clock(); fb = FakeBinance(clk)
    rc = T.main(['--once', '--symbols', 'btc,ETHUSDT', '--out', str(tmp_path)], transport=fb, sleep=clk.sleep, clock=clk)
    assert rc == 0
    m = MD.read_json(os.path.join(tmp_path, 'manifest.json'))
    assert m['last_run']['symbols'] == 2 and 'mark_klines/ETHUSDT/1h' in m['series']
    assert 'run done' in open(os.path.join(tmp_path, 'collector.log'), encoding='utf-8').read()
    assert MD.acquire_lock(str(tmp_path), clock=clk)                   # held by "another run" now
    assert T.main(['--once', '--symbols', 'BTCUSDT', '--out', str(tmp_path)], transport=fb, sleep=clk.sleep, clock=clk) == 3
    MD.release_lock(str(tmp_path))
    fb.script = [MD.Banned(60)]
    assert T.main(['--once', '--symbols', 'BTCUSDT', '--out', str(tmp_path)], transport=fb, sleep=clk.sleep, clock=clk) == 3
    with pytest.raises(SystemExit): T.main(['--every', '1m'])


def test_cli_default_universe_is_the_engine_top40():
    import collect_market_data as T, engine
    assert T.top40_from_engine() == engine.TOP40
    syms, why = T.resolve_symbols('')
    assert syms == engine.TOP40 and why == 'engine.py TOP40'          # conftest LOCALAPPDATA has no settings.json


def test_cli_testnet_snapshot_feeds_exchange_rules(tmp_path, monkeypatch):
    import collect_market_data as T
    clk = Clock(); fb = FakeBinance(clk)
    built = []
    fake = types.ModuleType('exchange_rules')
    def build_file(path, env, out=None, source=None, fetched_at=None, version=None):
        with open(path, encoding='utf-8') as f: info = json.load(f)
        built.append((env, source, fetched_at, len(info['symbols']))); return dict(symbols={}, version=1), None
    fake.build_file = build_file
    monkeypatch.setitem(sys.modules, 'exchange_rules', fake)
    assert T.main(['--testnet', '--out', str(tmp_path)], transport=fb, sleep=clk.sleep, clock=clk) == 0
    assert [x[0] for x in fb.calls] == ['/fapi/v1/exchangeInfo'], 'testnet mode fetches only exchangeInfo'
    assert built == [('testnet', MD.TESTNET + '/fapi/v1/exchangeInfo', '2026-10-07T00:00:00Z', 2)]
    assert os.path.exists(os.path.join(tmp_path, 'exchange_info', 'testnet_exchangeInfo_latest.json'))
    assert MD.read_json(os.path.join(tmp_path, 'manifest.json'))['series']['exchange_info/testnet']['source'] == MD.TESTNET
    monkeypatch.setitem(sys.modules, 'exchange_rules', None)          # not in this checkout -> prints the build command
    lines = []
    T.build_rules(os.path.join(tmp_path, 'x.json'), NOW, lines.append)
    assert 'exchange_rules.py build' in lines[0] and '--env testnet' in lines[0]


def test_requests_transport_maps_status_codes_and_never_sends_a_key():
    class R:
        def __init__(self, code, data, headers=None): self.status_code, self._d, self.headers = code, data, headers or {}; self.content = b'x'
        def json(self): return self._d
    class S:
        def __init__(self): self.headers, self.q = {'X-MBX-APIKEY': 'leak'}, []
        def get(self, url, params=None, timeout=None): self.q.append(url); return self.q_resp.pop(0)
    s = S()
    t = MD.RequestsTransport(session=s)
    assert 'X-MBX-APIKEY' not in s.headers
    s.q_resp = [R(418, {'code': -1003}, {'Retry-After': '120'}), R(429, {}, {'Retry-After': '3'}), R(503, None),
                R(400, {'code': -1121, 'msg': 'Invalid symbol.'}), R(200, [1])]
    with pytest.raises(MD.Banned) as b: t.get('/x', {})
    assert b.value.retry_after == 120
    with pytest.raises(MD.RateLimited) as r: t.get('/x', {})
    assert r.value.retry_after == 3
    with pytest.raises(MD.Transient): t.get('/x', {})
    with pytest.raises(MD.Refused): t.get('/x', {})
    assert t.get('/x', {}) == [1]


# ------------------------------------------------------------------ in-app collector
class _Resp:
    def __init__(self, code, data, headers=None): self.status_code, self._d, self.headers = code, data, headers or {}; self.content = b'x'
    def json(self): return self._d


def _real_futures(fb):
    """A real binance_client.Futures (keyless MAINNET, no network): _once answers from the fake Binance."""
    f = BC.Futures.__new__(BC.Futures)
    f.key, f.secret, f.base, f.rw, f.offset, f.last_ok = '', b'', BC.MAINNET, 6000, 0, 0.0
    f.s = None
    def once(method, url, params, signed):
        assert method == 'GET' and not signed, 'public reads only'
        path = url[len(BC.MAINNET):]
        try:
            d = fb.get(path, params)
            return _Resp(200, d), d
        except MD.Banned as b: return _Resp(418, {'code': -1003, 'msg': 'banned'}, {'Retry-After': str(int(b.retry_after))}), {'code': -1003, 'msg': 'banned'}
        except MD.RateLimited as r: return _Resp(429, {'code': -1003, 'msg': 'too many'}, {'Retry-After': '1'}), {'code': -1003, 'msg': 'too many'}
        except MD.Refused as x: return _Resp(400, {'code': x.code, 'msg': 'no'}), {'code': x.code, 'msg': 'no'}
    f._once = once
    return f


class GuardLock:
    """engine.lock stand-in: acquiring it from the collector thread is a test failure."""
    def __init__(self): self.bad = []
    def _chk(self):
        if threading.current_thread().name == 'market-collector': self.bad.append('acquired from the collector thread')
        return True
    def acquire(self, *a, **k): return self._chk()
    def release(self): pass
    def __enter__(self): self._chk(); return self
    def __exit__(self, *a): return False


def _engine(fb, on=True):
    e = types.SimpleNamespace(S=dict(MARKET_COLLECTOR=on, UNIVERSE=['BTCUSDT', 'ETHUSDT']), lock=GuardLock(),
                              data=_real_futures(fb), state=dict(lots={}), trade=object())
    e.orders_touched = []
    return e


def _fast(mc, clk):
    mc.WEIGHT_PER_MIN = 1e9
    mc._sleep = clk.sleep
    return mc


def test_in_app_thread_collects_without_ever_taking_engine_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(BC.time, 'sleep', lambda s: None)
    clk = Clock(); fb = FakeBinance(clk, symbols=('BTCUSDT', 'ETHUSDT'))
    fb.onboard = NOW - 20 * MD.DAY_MS
    e = _engine(fb)
    mc = _fast(MC.MarketCollector(lambda: e, str(tmp_path / 'md'), every_s=3600, first_delay_s=0, clock=clk), clk)
    done = threading.Event()
    real = mc.run_once
    mc.run_once = lambda: (real(), done.set(), mc.stop())
    mc.start()
    assert done.wait(60), 'collector run did not finish'
    mc.thread.join(5)
    assert e.lock.bad == [], e.lock.bad
    st = mc.status()
    assert st['runs'] == 1 and st['last_run']['rows_added'] > 0 and st['last_run']['errors'] == 0 and st['enabled'] is True
    assert st['running'] is False and st['out'].endswith('md')
    m = MD.read_json(os.path.join(tmp_path, 'md', 'manifest.json'))
    assert m['last_run']['kind'] == 'in-app' and m['series']['mark_klines/ETHUSDT/4h']['rows'] == 20 * 6
    assert {p for p, _ in fb.calls} <= {'/fapi/v1/exchangeInfo', '/fapi/v1/fundingInfo', '/fapi/v1/fundingRate',
                                        '/fapi/v1/markPriceKlines'} | {MD.DATASETS[d]['path'] for d in MD.DATASETS}
    assert e.data.health.state == 'ok'
    assert not os.path.exists(os.path.join(tmp_path, 'md', MD.LOCK_FILE))


def test_in_app_418_through_the_real_client_stops_and_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(BC.time, 'sleep', lambda s: None)
    clk = Clock(); fb = FakeBinance(clk)
    e = _engine(fb)
    fb.script = [None, MD.Banned(900)]
    mc = _fast(MC.MarketCollector(lambda: e, str(tmp_path), clock=clk), clk)
    s = mc.run_once()
    assert s['stopped'] and '418' in s['stopped'] and len(fb.calls) == 2, 'the ban was not retried'
    assert mc.status()['banned_until']


def test_in_app_429_through_the_real_client_backs_off_and_respects_the_engine_circuit(tmp_path, monkeypatch):
    """A 429 marks the engine's shared circuit 'degraded'. The collector honours Retry-After, then waits (bounded) for the
    engine's next good read before sending anything else; if it never comes, the run stops instead of adding load."""
    monkeypatch.setattr(BC.time, 'sleep', lambda s: None)
    clk = Clock(); fb = FakeBinance(clk, symbols=('BTCUSDT',))
    e = _engine(fb); e.S['UNIVERSE'] = ['BTCUSDT']
    fb.script = [None, None, MD.RateLimited(1)]
    mc = _fast(MC.MarketCollector(lambda: e, str(tmp_path), clock=clk), clk)
    mc.DEGRADED_WAIT_S = 0
    s = mc.run_once()
    assert s['stopped'] == 'engine exchange circuit degraded - not adding load' and len(fb.calls) == 3
    assert any(abs(x - 1.0) < 1e-9 for x in clk.slept), clk.slept
    # the engine's own next read succeeds while the collector waits -> the run carries on
    fb.calls.clear(); fb.script = [None, None, MD.RateLimited(1)]
    mc.DEGRADED_WAIT_S = 20
    e.data.health.ok()                                                  # (the engine read fine since)
    threading.Timer(0.3, e.data.health.ok).start()
    s = mc.run_once()
    assert s['stopped'] is None and s['errors'] == 0 and len(fb.calls) > 3


def test_setting_off_disables_and_switching_off_mid_run_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(BC.time, 'sleep', lambda s: None)
    clk = Clock(); fb = FakeBinance(clk)
    e = _engine(fb, on=False)
    mc = _fast(MC.MarketCollector(lambda: e, str(tmp_path), clock=clk), clk)
    assert mc.run_once() is None and fb.calls == [] and mc.status()['last_skip']['why'] == 'MARKET_COLLECTOR off'
    assert mc.status()['enabled'] is False
    e.S['MARKET_COLLECTOR'] = True
    orig = fb.get
    def get(p, q):
        if len(fb.calls) == 3: e.S['MARKET_COLLECTOR'] = False             # owner flips the switch during the run
        return orig(p, q)
    fb.get = get
    s = mc.run_once()
    assert s['stopped'] == 'MARKET_COLLECTOR switched off' and len(fb.calls) == 4, 'the request in flight ends, no new one'
    e.S['MARKET_COLLECTOR'] = 'yes'                                         # anything but True counts as off
    assert not mc.enabled()


def test_engine_circuit_outage_means_no_requests(tmp_path):
    clk = Clock(); fb = FakeBinance(clk)
    e = _engine(fb)
    h = e.data.health
    h.state = 'outage'; h.next_probe = time.monotonic() + 999
    mc = _fast(MC.MarketCollector(lambda: e, str(tmp_path), clock=clk), clk)
    s = mc.run_once()
    assert s['stopped'] and 'circuit' in s['stopped'] and fb.calls == []


def test_overlapping_run_is_skipped(tmp_path):
    clk = Clock(); fb = FakeBinance(clk)
    e = _engine(fb)
    mc = _fast(MC.MarketCollector(lambda: e, str(tmp_path), clock=clk), clk)
    mc._run_lock.acquire()
    assert mc.run_once() is None and mc.status()['last_skip']['why'] == 'previous run still going'
    mc._run_lock.release()
    MD.acquire_lock(str(tmp_path), clock=clk)
    assert mc.run_once() is None and 'standalone' in mc.status()['last_skip']['why'] and fb.calls == []


def test_run_failure_is_one_log_line_and_the_thread_survives(tmp_path):
    import logging
    clk = Clock()
    e = types.SimpleNamespace(S=dict(MARKET_COLLECTOR=True, UNIVERSE=['BTCUSDT']), data=object(), lock=GuardLock())
    mc = MC.MarketCollector(lambda: e, str(tmp_path), clock=clk, transport_factory=lambda d: 1 / 0)
    got = []
    class Hd(logging.Handler):
        def emit(self, r): got.append(r.getMessage())
    hd, lg = Hd(level=0), logging.getLogger('zackbot')
    lg.addHandler(hd); old = lg.level; lg.setLevel(logging.INFO)
    try: assert mc.run_once() is None
    finally: lg.removeHandler(hd); lg.setLevel(old)
    assert mc.status()['error'].startswith('ZeroDivisionError')
    assert [m for m in got if 'market data' in m] == ['market data: run failed - ZeroDivisionError: division by zero']
    assert not os.path.exists(os.path.join(tmp_path, MD.LOCK_FILE)) and mc._run_lock.acquire(blocking=False)


# ------------------------------------------------------------------ app wiring: setting, status, start-up
def _app_engine(monkeypatch, tmp):
    import engine as E
    class FX:
        def __init__(self, *a, **k): self.base = BC.MAINNET
    monkeypatch.setattr(E, 'Futures', FX)
    return E.Engine(dict(MODE='paper'), tmp)


def test_setting_default_on_and_validated(monkeypatch, tmp_path):
    import app as A
    e = _app_engine(monkeypatch, str(tmp_path))
    assert e.S['MARKET_COLLECTOR'] is True
    with open(e.F['settings'], 'w', encoding='utf-8') as f: json.dump(dict(MARKET_COLLECTOR='off'), f)
    e.load_settings(); assert e.S['MARKET_COLLECTOR'] is True, 'a corrupt value falls back to the default'
    app = A.App.__new__(A.App); app.engine = e; app.preview = lambda: None
    monkeypatch.setattr(A, 'APP', app, raising=False)
    with pytest.raises(ValueError): A.handle('/api/settings', dict(MARKET_COLLECTOR='no'))
    assert e.S['MARKET_COLLECTOR'] is True
    A.handle('/api/settings', dict(MARKET_COLLECTOR=False))
    assert e.S['MARKET_COLLECTOR'] is False
    assert json.load(open(e.F['settings'], encoding='utf-8'))['MARKET_COLLECTOR'] is False


def test_status_is_exposed_in_api_health(monkeypatch, tmp_path):
    import app as A
    e = _app_engine(monkeypatch, str(tmp_path))
    app = A.App.__new__(A.App); app.engine = e; app.loop_ok = time.time()
    assert app.health(e, [])['market_collector'] is None                 # older App objects without a collector
    app.collector = MC.MarketCollector(lambda: e, str(tmp_path / 'md'))
    app.collector._skip('MARKET_COLLECTOR off')
    h = app.health(e, [])['market_collector']
    assert h['enabled'] is True and h['runs'] == 0 and h['last_skip']['why'] == 'MARKET_COLLECTOR off'
    json.dumps(h)


def test_app_starts_the_collector_after_the_engine_and_selftest_imports_it():
    src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
    main = src[src.index('def main():'):]
    assert main.index('APP = App()') < main.index("APP.collector = MC.MarketCollector(lambda: APP.engine, os.path.join(DATA, 'market_data'))") \
        < main.index('APP.collector.start()')
    assert 'market_collector, market_data' in src[src.index('def selftest('):src.index('def main():')]
    col = open(os.path.join(ROOT, 'market_collector.py'), encoding='utf-8').read()
    import ast
    attrs = {n.attr for n in ast.walk(ast.parse(col)) if isinstance(n, ast.Attribute)}
    assert 'lock' not in attrs, 'the collector never references engine.lock'
    assert not attrs & {'trade', 'state', 'lots', 'save_state', 'open_lot', 'close_lot', 'cancel', 'cancel_all', '_order', 'stop_order'}
    assert 'signed=True' not in col


def test_installer_keeps_collected_data_out_of_the_build():
    import re
    src = open(os.path.join(ROOT, 'installer.ps1'), encoding='utf-8').read()
    xd = re.search(r"'/XD',(.*?)'/XF'", src, re.S).group(1)
    assert "'data_market'" in xd
    assert 'data_market/' in open(os.path.join(ROOT, '.gitignore'), encoding='utf-8').read()
