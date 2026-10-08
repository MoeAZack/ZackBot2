"""AUD-07 C13b: the BTC circuit breaker (and the pump guard) are sampled like the live engine - on EVERY closed 1h BTC
candle - and the breaker pause is wall-clock, as long as the engine's.

Engine (engine.py Engine._breaker / btc_move_1h): every manage pass (~8 s) reads the move of the LAST CLOSED 1h BTC candle
and, while it is above pct, sets breaker_until = max(until, now + hours). That candle stays the last closed one for a full
hour, so the pause runs from the trip candle's close ct to ct + 1 h + hours. Before this fix the backtester looked only at
the last hour of each 4h candle (or at |4h move| / 2 when the app / Lab passed no 1h data) and paused for
ceil(hours / 4) whole candles.

Cases (4h candles; BTC flat at 100 except the hour candles named; ETH signals):
- a +6 % hour inside a 4h candle that closes flat must block the ETH signal of that candle (live refuses it);
- the pause length: for a trip in each hour of a candle and hours 1-8, the decision at every later candle close is paused
  exactly when the real engine (driven every 8 s) is paused at that cycle;
- without 1h data the result says it used a proxy (cv.attrs['btc_move_source'] == 'proxy'); the app job and Lab load BTC 1h
  candles for a 4h run that uses a BTC-move rule and report the source.
"""
import bisect
import copy
import os
import types

import numpy as np
import pandas as pd
import pytest

import backtest as B
from _aud07_twin import market

I = 270                                         # the candle that holds the BTC spike (4h index)
RULE = {'mode': 'enforce', 'pct': 5.0, 'hours': 4, 'tighten': False}


def _btc1h(raw, steps):
    """BTC 1h candles under the 4h book (4h candle k holds 1h candles 4k..4k+3): flat, and from each {1h index: close} in
    `steps` on flat at that close. Only these 1h candles drive the BTC-move rules when given (the 4h BTC book stays flat)."""
    b = raw['BTCUSDT']
    rows, px = [], float(b.c[0])
    for k in range(len(b)):
        for j in range(4):
            h = 4 * k + j
            c = steps.get(h, px)
            rows.append(dict(t=b.t[k] + pd.Timedelta(hours=j), o=px, h=max(px, c), l=min(px, c), c=c, v=1.0))
            px = c
    return pd.DataFrame(rows)


def _raw():
    return market({'BTCUSDT': (100, 0.5, {}), 'ETHUSDT': (200, 1, {})})


def _run(raw, signals, rule=RULE, btc1h=None, **kw):
    bk = B.Book(copy.deepcopy(raw))
    for s in bk.syms:
        g = {k: np.zeros(len(raw[s]), bool) for k in ('le', 'se', 'lx', 'sx')}
        for bar, sym, kind in signals:
            if sym == s: g[kind][bar] = True
        bk._sig[('ema_st', s, 'None')] = g
    cfg = [dict(key='ema_st', share=1.0, risk=0.02, max_pos=1, symbols=['ETHUSDT'], sides='long', mgmt={'stop_atr': 30.0, 'max_bars': 2})]
    tr, cv = B.run(bk, cfg, start=500.0, t0=raw['BTCUSDT'].t[261], fund_per_bar=0.0,
                   risk_rules={'btc_breaker': rule} if rule else None, btc1h=btc1h, **kw)
    return (sorted(int(x) for x in tr.i_in) if len(tr) else []), cv.attrs


# ------------------------------------------------------------------ 1) an hourly spike inside a flat 4h candle trips
SPIKE = {4 * I: 106.0, 4 * I + 1: 104.0, 4 * I + 2: 102.0, 4 * I + 3: 100.0}     # +6 %, -1.9 %, -1.9 %, -2.0 %; 4h close flat


def test_hourly_spike_inside_a_flat_4h_candle_blocks_the_signal():
    raw = _raw()
    h = _btc1h(raw, SPIKE)
    got, at = _run(raw, [(I, 'ETHUSDT', 'le')], btc1h=h)
    assert got == [] and at['blocked'] == {'btc_breaker': 1}, (got, at['blocked'])      # live refuses it (was: entered at I+1)
    assert at['btc_move_source'] == '1h'
    calm, at2 = _run(raw, [(I, 'ETHUSDT', 'le')], btc1h=_btc1h(raw, {}))
    assert calm == [I + 1] and at2['blocked'] == {}                                      # control: no spike -> entered


def test_pump_guard_reads_the_last_closed_1h_candle():
    """The pump guard (signal time) uses the last 1h candle closed at the decision: the +6 % first hour does not trip it
    (the engine sees the -2 % last hour), a +6 % LAST hour does."""
    raw = _raw()
    pg = {'btc_1h_pct': 5}
    first, _ = _run(raw, [(I, 'ETHUSDT', 'le')], rule=None, btc1h=_btc1h(raw, SPIKE), pump_guard=pg)
    last, at = _run(raw, [(I, 'ETHUSDT', 'le')], rule=None, btc1h=_btc1h(raw, {4 * I + 3: 106.0}), pump_guard=pg)
    assert first == [I + 1] and last == [] and at['blocked'] == {'pump_btc': 1}


# ------------------------------------------------------------------ 2) the pause is as long as the engine's
def _engine_paused(raw, h1, rule, at_times, step=8.0, cache=False, since=None):
    """The REAL Engine._breaker() on the 1h candles, called every `step` s (the app's manage pass while the bot holds
    anything). Returns ({t: active at t} for t in at_times, breaker_until). cache=False clears btc_move_1h's 60 s cache before
    every pass (an idealised, latency-free engine: the 60 s cache only delays a trip / an end by < 60 s)."""
    import engine as E
    ms = (h1.t.values.astype('datetime64[ns]').astype('int64') // 10 ** 6)
    kl = [[int(t), r.o, r.h, r.l, r.c, r.v, int(t) + 3600_000 - 1] for t, r in zip(ms, h1.itertuples())]
    now = [0.0]
    fake = types.SimpleNamespace(state={}, _btc1h=None, notify=lambda *a, **k: None, save_state=lambda: None,
                                 _tighten_all=lambda: None, risk_rules_cfg=lambda: {'btc_breaker': rule})
    fake.data = types.SimpleNamespace(klines=lambda s, tf, n: kl[max(0, bisect.bisect_right(ms, now[0] * 1000) - n):
                                                                  bisect.bisect_right(ms, now[0] * 1000)])
    fake.btc_move_1h = lambda: E.Engine.btc_move_1h(fake)
    saved = E.time
    E.time = types.SimpleNamespace(time=lambda: now[0], sleep=lambda x: None)
    out = {}
    try:
        ts = sorted(at_times)
        t = (ts[0] - 6 * 3600 if since is None else since) + 3.0
        for target in ts:
            while t < target:
                now[0] = t
                if not cache: fake._btc1h = None
                E.Engine._breaker(fake)
                t += step
            now[0] = target
            if not cache: fake._btc1h = None
            out[target] = bool(E.Engine._breaker(fake)['active'])         # the cycle's own _rules_eval call
    finally:
        E.time = saved
    return out, fake.state.get('breaker_until')


@pytest.mark.parametrize('hours', [1, 2, 3, 4, 6, 8])
@pytest.mark.parametrize('hour_in_candle', [0, 1, 2, 3])
def test_pause_matches_the_engine_at_every_candle_close(hour_in_candle, hours):
    raw = _raw()
    h1 = _btc1h(raw, {4 * I + hour_in_candle: 106.0})
    rule = dict(RULE, hours=hours)
    T = raw['BTCUSDT'].t
    closes = {j: (T[j] + pd.Timedelta(hours=4)).timestamp() for j in range(I - 1, I + 5)}
    eng, _ = _engine_paused(raw, h1, rule, [c + 15 for c in closes.values()])          # cycles run at close + 15 s
    bk = B.Book(copy.deepcopy(raw))
    w = B.btc_breaker_windows(bk, 'BTCUSDT', 14400.0, rule, h1)
    for j, c in closes.items():
        assert bool(w['close'][j]) == eng[c + 15], (j - I, hours, hour_in_candle, eng)
    # intrabar events of candle j use the pause state at its open (a pause starting mid-candle applies from the next one)
    for j in range(I, I + 5):
        assert bool(w['open'][j]) == bool(w['close'][j - 1])
    assert w['new'][I] and w['new'].sum() == 1 and w['trips'] == 1


def test_engine_pause_ends_one_hour_after_the_trip_candle_plus_hours():
    """The rule the backtester copies, measured on the real engine WITH its 60 s data cache: the pause ends at
    trip close + 1 h + hours, within the cache's 60 s."""
    raw = _raw()
    h1 = _btc1h(raw, {4 * I + 1: 106.0})
    ct = (raw['BTCUSDT'].t[I] + pd.Timedelta(hours=2)).timestamp()
    _, until = _engine_paused(raw, h1, RULE, [ct + 10 * 3600], cache=True, since=ct - 3600)
    assert 0 <= until - (ct + 3600 + 4 * 3600) <= 70, (until - ct) / 3600


@pytest.mark.parametrize('hours', [2, 4, 6, 8])
def test_end_to_end_a_trip_in_the_last_hour(hours):
    """Trip in the LAST hour of candle I (closes at the candle close). Engine pause: close + 1 h + hours. The ETH signal of
    candle I+1 (4 h later) is refused only when 1 + hours > 4; the one of I+2 (8 h later) only when 1 + hours > 8.
    Before: ceil(hours / 4) whole candles (hours 2 -> I+1 refused, hours 6 -> I+2 refused, both after live resumed)."""
    raw = _raw()
    h1 = _btc1h(raw, {4 * I + 3: 106.0})
    rule = dict(RULE, hours=hours)
    want = {1: 1 + hours > 4, 2: 1 + hours > 8}
    ct = (raw['BTCUSDT'].t[I] + pd.Timedelta(hours=4)).timestamp()
    eng, _ = _engine_paused(raw, h1, rule, [ct + 4 * 3600 + 15, ct + 8 * 3600 + 15])
    for k, paused in want.items():
        got, at = _run(raw, [(I + k, 'ETHUSDT', 'le')], rule=rule, btc1h=h1)
        assert got == ([] if paused else [I + k + 1]), (k, got, at['blocked'])
        assert eng[ct + 4 * k * 3600 + 15] == paused, k                              # the real engine agrees


def test_tighten_runs_once_per_pause_episode():
    """A second trip inside the running pause extends it (engine: max(until, now + hours)) but is not a new episode."""
    raw = _raw()
    up = lambda h: {h: 106.0, h + 1: 104.0, h + 2: 102.0, h + 3: 100.0}            # +6 % then back down in < 5 % steps
    h1 = _btc1h(raw, {**up(4 * I), **up(4 * I + 4)})          # trips close at T_I + 1 h and T_I + 5 h (pause until T_I + 6 h)
    w = B.btc_breaker_windows(B.Book(copy.deepcopy(raw)), 'BTCUSDT', 14400.0, RULE, h1)
    assert w['trips'] == 2 and list(np.where(w['new'])[0]) == [I]
    assert list(np.where(w['close'][I - 1:I + 5])[0] + I - 1) == [I, I + 1]          # extended to T_I + 10 h: closes +4 h, +8 h


# ------------------------------------------------------------------ 3) no 1h data -> the result says it used a proxy
def test_proxy_is_labelled():
    raw = _raw()
    _, at = _run(raw, [(I, 'ETHUSDT', 'le')])
    assert at['btc_move_source'] == 'proxy'
    _, at = _run(raw, [(I, 'ETHUSDT', 'le')], rule=None, pump_guard={'btc_1h_pct': 3})
    assert at['btc_move_source'] == 'proxy'
    _, at = _run(raw, [(I, 'ETHUSDT', 'le')], rule=None)
    assert at['btc_move_source'] is None                                             # no BTC-move rule in the run
    one_h = {s: d.assign(t=pd.date_range('2024-01-01', periods=len(d), freq='1h')) for s, d in raw.items()}
    _, at = _run(one_h, [(I, 'ETHUSDT', 'le')])
    assert at['btc_move_source'] == 'own'                                            # a 1h book's own BTC candles
    assert B.needs_btc_move([{}], None, {'btc_breaker': RULE}) and B.needs_btc_move([{'pump_guard': {'btc_1h_pct': 2}}])
    assert not B.needs_btc_move([{}], {'max_candle_atr': 3}, {'btc_breaker': dict(RULE, mode='warn')})


def test_one_hour_book_keeps_its_candle_arithmetic():
    """A 1h book: every candle IS a 1h BTC candle; the engine pause close + 1 h + hours = the old i .. i + hours candles."""
    raw = {s: d.assign(t=pd.date_range('2024-01-01', periods=len(d), freq='1h')) for s, d in _raw().items()}
    raw['BTCUSDT'].loc[I, ['h', 'c']] = [106.5, 106.0]
    raw['BTCUSDT'].loc[I + 1:, ['o', 'c']] = 106.0
    raw['BTCUSDT'].loc[I + 1:, 'h'] = 106.5; raw['BTCUSDT'].loc[I + 1:, 'l'] = 105.5
    w = B.btc_breaker_windows(B.Book(copy.deepcopy(raw)), 'BTCUSDT', 3600.0, RULE)
    assert list(np.where(w['close'])[0]) == [I, I + 1, I + 2, I + 3, I + 4]


# ------------------------------------------------------------------ 4) app job + Lab load BTC 1h and report the source
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _csv_candles(sym, tf, days):
    d = pd.read_csv(os.path.join(ROOT, 'data' if tf == '4h' else 'data1h', f'{sym}_{tf}.csv'), parse_dates=['t'])
    return d.tail(int(days * 86400 / (14400 if tf == '4h' else 3600)) + 260 * (1 if tf == '4h' else 4)).reset_index(drop=True)


def _app_job(monkeypatch, A, run_options, candles):
    monkeypatch.setattr(A, 'APP', None)
    monkeypatch.setattr(A, 'get_candles', candles)
    monkeypatch.setattr(A, 'exchange_rules_now', lambda e=None: (None, 'off', 'test'))
    jid = '20260101-000000-c13b'
    A.JOBS[jid] = dict(status='queued')
    A.run_backtest_job(jid, dict(name='c13b', sleeves=[dict(key='ema_st', share=1, risk=0.01, max_pos=2, symbols=['ETHUSDT'])],
                                 days=60, tf='4h', start=500, universe=['ETHUSDT'], exchange_rules='off', run_options=run_options))
    j = A.JOBS.pop(jid)
    assert j['status'] == 'done', j
    return j['result']


def test_app_backtest_job_loads_btc_1h_and_reports_the_source(monkeypatch):
    import app as A
    rr = {'risk_rules': {'btc_breaker': dict(RULE)}}
    calls = []

    def candles(sym, tf, days):
        calls.append((sym, tf)); return _csv_candles(sym, tf, days)
    assert _app_job(monkeypatch, A, rr, candles)['btc_move_source'] == {'4h': '1h'}
    assert ('BTCUSDT', '1h') in calls

    def no_1h(sym, tf, days):
        if tf == '1h': raise OSError('offline')
        return _csv_candles(sym, tf, days)
    assert _app_job(monkeypatch, A, rr, no_1h)['btc_move_source'] == {'4h': 'proxy'}
    calls.clear()
    assert _app_job(monkeypatch, A, {}, candles)['btc_move_source'] == {}           # no BTC-move rule: nothing loaded
    assert ('BTCUSDT', '1h') not in calls


def test_btc1h_for_backtest_keeps_closed_candles_only(monkeypatch):
    import app as A
    h = pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=10, freq='1h'), o=1.0, h=1.0, l=1.0, c=1.0, v=1.0))
    monkeypatch.setattr(A, 'get_candles', lambda s, tf, d: h)
    got = A.btc1h_for_backtest(30, 14400, now=pd.Timestamp('2024-01-01 09:30').timestamp())
    assert list(got.columns) == ['t', 'c'] and got.t.iloc[-1] == pd.Timestamp('2024-01-01 08:00')   # 09:00 is still forming


def test_lab_passes_btc_1h_and_reports_the_source(monkeypatch):
    import app as A
    import lab
    monkeypatch.setattr(A, 'get_candles', _csv_candles)
    req = dict(tf='4h', days=60, sleeves=[dict(key='ema_st', share=1, risk=0.01, max_pos=2, symbols=['ETHUSDT'])],
               run_options={'risk_rules': {'btc_breaker': dict(RULE)}})
    q = lab.validate_lab_request('liquidation', req)
    bk = A.lab_book(q)
    assert bk.btc1h is not None and len(bk.btc1h)
    seen = []
    real = B.run

    def spy(book, sleeves, **kw):
        seen.append(kw.get('btc1h') is not None); return real(book, sleeves, **kw)
    monkeypatch.setattr(B, 'run', spy)
    r = lab.run_lab_job('liquidation', req, lambda q_: bk)
    assert r['btc_move_source'] == '1h' and seen and all(seen)
    bk.btc1h = None
    assert lab.run_lab_job('liquidation', req, lambda q_: bk)['btc_move_source'] == 'proxy'
