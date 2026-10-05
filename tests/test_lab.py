"""Backtest Lab tests - deterministic, synthetic data (plus one small real-data slice for the lookahead check)."""
import json, math, os, sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import backtest as BT
import strategies as S
import lab


def toy_raw(n=1500, syms=('BTCUSDT', 'ETHUSDT', 'SOLUSDT'), seed=1):
    rng = np.random.default_rng(seed)
    t = pd.date_range('2023-01-01', periods=n, freq='4h')
    out = {}
    for k, s in enumerate(syms):
        drift = 0.0006 * np.sin(np.arange(n) / 150 + k)                 # alternating trends -> crosses happen
        r = drift + rng.normal(0, 0.012, n)
        c = 100 * (k + 1) * np.exp(np.cumsum(r))
        o = np.r_[c[0], c[:-1]]
        h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, n)))
        l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, n)))
        out[s] = pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=rng.uniform(1e3, 2e3, n)))
    return out


@pytest.fixture(scope='module')
def book():
    return BT.Book(toy_raw())


def base_sleeves(book):
    return lab.resolve_sleeves([dict(key='ema_st', share=1, risk=0.01, max_pos=3, symbols='all', mgmt=dict(stop_atr=2.5))], book)


def fake_run_factory(best=3.0, n_trades=40):
    """backtest.run stand-in: the curve grows fastest when sleeve 0's stop_atr == best (with small wiggles for a DD)."""
    def fake(book, sleeves, start=500.0, t0=None, t1=None, warmup=220, **kw):
        T = book.t.values
        idx = np.arange(warmup, len(T))
        if t0 is not None: idx = idx[T[idx] >= np.datetime64(t0)]
        if t1 is not None: idx = idx[T[idx] < np.datetime64(t1)]
        sa = float(sleeves[0]['mgmt'].get('stop_atr', 2.5))
        g = 0.002 - 0.0004 * (sa - best) ** 2
        steps = 1 + g + 0.003 * np.sin(idx / 5.0)
        cv = pd.Series(start * np.cumprod(steps), index=pd.to_datetime(T[idx]))
        k = np.linspace(0, len(idx) - 1, n_trades).astype(int) if len(idx) else np.zeros(0, int)
        tr = pd.DataFrame(dict(sleeve='ema_st', sym='BTCUSDT', side=1, i_in=idx[k], i_out=idx[k], pnl=np.where(np.arange(len(k)) % 3, 2.0, -1.0),
                               R=np.where(np.arange(len(k)) % 3, 1.0, -0.5), why='signal'))
        return tr, cv
    return fake


# ------------------------------------------------------------------ optimize
def test_optimize_finds_known_best(book, monkeypatch):
    monkeypatch.setattr(lab.BT, 'run', fake_run_factory(best=3.0))
    space = [dict(path='sleeves[0].mgmt.stop_atr', values=dict(min=1.0, max=5.0, step=0.5))]
    for metric in ('calmar', 'return', 'return_dd', 'sharpe'):
        res = lab.optimize(book, base_sleeves(book), space, metric=metric, method='grid', n_trials=60, min_trades=10)
        assert res['space_size'] == 9 and res['evaluated'] == 9
        top = res['results'][0]
        assert top['params'] == {'sleeves[0].mgmt.stop_atr': 3.0}, (metric, top)
        assert [r['rank'] for r in res['results']] == list(range(1, 10))
        assert 'holdout_stats' in top and top['holdout_stats']['trades'] > 0
    # hold-out = last 25%, never used for ranking
    a, b = map(pd.Timestamp, res['period']['train']); c, d = map(pd.Timestamp, res['period']['holdout'])
    assert b == c and abs((d - c) / (d - a) - 0.25) < 0.01
    json.dumps(lab._js(res))


def test_optimize_min_trades_rejects(book, monkeypatch):
    monkeypatch.setattr(lab.BT, 'run', fake_run_factory(n_trades=5))
    res = lab.optimize(book, base_sleeves(book), [dict(path='sleeves[0].risk', values=[0.01, 0.02])], min_trades=30)
    assert res['results'] == [] and res['rejected'] == 2


def test_optimize_random_seeded_and_real_run(book):
    space = [dict(path='sleeves[0].mgmt.stop_atr', values=[2.0, 3.0, 4.0]), dict(path='sleeves[0].risk', values=[0.01, 0.02])]
    r1 = lab.optimize(book, base_sleeves(book), space, n_trials=4, seed=5, min_trades=1, metric='profit_factor')
    r2 = lab.optimize(book, base_sleeves(book), space, n_trials=4, seed=5, min_trades=1, metric='profit_factor')
    assert [x['params'] for x in r1['results']] == [x['params'] for x in r2['results']]
    assert r1['n_trials'] == 4 and r1['space_size'] == 6
    json.dumps(lab._js(r1))


def test_share_paths_renormalise(book):
    sl = lab.resolve_sleeves([dict(key='ema_st', share=.5, risk=.01, max_pos=3), dict(key='ema_mom', share=.5, risk=.01, max_pos=3)], book)
    out = lab._apply(sl, {'sleeves[0].share': 0.9})
    assert abs(sum(x['share'] for x in out) - 1) < 1e-9 and out[0]['share'] > out[1]['share']
    out = lab._apply(sl, {'sleeves[1].mgmt.pyramid.n': 2, 'sleeves[0].params.k': 7})
    assert out[1]['mgmt']['pyramid'] == dict(n=2, step_r=1.5, frac=0.5) and out[0]['params']['k'] == 7 and isinstance(out[0]['params']['k'], int)
    assert sl[1]['mgmt'] == {}                                           # base untouched


# ------------------------------------------------------------------ walk-forward
def test_wf_window_arithmetic():
    t0 = pd.Timestamp('2024-01-01')
    w = lab.wf_windows(t0, t0 + pd.Timedelta(days=500), 180, 60, 60)
    assert len(w) == 5
    for k, (trn, tst) in enumerate(w):
        assert trn[0] == t0 + pd.Timedelta(days=60 * k) and trn[1] == tst[0] == trn[0] + pd.Timedelta(days=180)
        assert tst[1] - tst[0] == pd.Timedelta(days=60)
    assert all(w[k][1][1] <= w[k + 1][1][0] for k in range(len(w) - 1))      # test windows never overlap
    # a final short test window is kept only if >= half a test window
    assert len(lab.wf_windows(t0, t0 + pd.Timedelta(days=270), 180, 60, 60)) == 2      # [180,240) + [240,270)
    assert len(lab.wf_windows(t0, t0 + pd.Timedelta(days=269), 180, 60, 60)) == 1
    with pytest.raises(ValueError): lab.wf_windows(t0, t0 + pd.Timedelta(days=500), 180, 60, 30)


def test_wf_chaining(book, monkeypatch):
    monkeypatch.setattr(lab.BT, 'run', fake_run_factory(best=3.0))
    space = [dict(path='sleeves[0].mgmt.stop_atr', values=[2.0, 3.0, 4.0])]
    res = lab.walk_forward(book, base_sleeves(book), space, train_days=60, test_days=20, step_days=20, n_trials=3, min_trades=5, start=500.0)
    W = res['windows']
    assert len(W) >= 3
    assert W[0]['start_cap'] == 500.0
    for a, b in zip(W, W[1:]):
        assert a['start_cap'] < a['end_cap'] and abs(b['start_cap'] - a['end_cap']) < 0.01      # chained from the ending capital
    assert all(w['best_params'] == {'sleeves[0].mgmt.stop_atr': 3.0} for w in W)
    sm = res['summary']
    assert abs(sm['oos_end'] - W[-1]['end_cap']) < 0.01
    exp = np.prod([1 + w['test_ret'] / 100 for w in W]) * 500
    assert abs(exp - sm['oos_end']) / sm['oos_end'] < 1e-3
    assert sm['pct_profitable_windows'] == 100.0 and sm['efficiency'] is not None
    assert res['curve'] and res['curve'][-1][1] == pytest.approx(sm['oos_end'], rel=1e-3)
    json.dumps(lab._js(res))


# ------------------------------------------------------------------ Monte Carlo
def mc_trades(n=200, seed=3):
    rng = np.random.default_rng(seed)
    f = np.where(rng.random(n) < 0.55, rng.uniform(0.005, 0.03, n), -rng.uniform(0.005, 0.02, n))
    eq = 500 * np.cumprod(np.r_[1, 1 + f[:-1]])
    return pd.DataFrame(dict(i_in=np.arange(n), i_out=np.arange(n), pnl=f * eq, eq_in=eq)), f


def test_mc_shuffle_preserves_end_dd_varies():
    tr, f = mc_trades()
    r = lab.monte_carlo(tr, start=500, n=500, method='shuffle', seed=1)
    end = 500 * np.prod(1 + f)
    assert r['end_capital']['p5'] == pytest.approx(end, abs=0.01) and r['end_capital']['p95'] == pytest.approx(end, abs=0.01)
    assert r['actual']['end'] == pytest.approx(end, abs=0.01)
    assert r['max_dd']['p5'] < r['max_dd']['p95']                                  # path risk varies
    assert r['losing_streak']['p95'] >= r['losing_streak']['p5']
    assert 0 <= r['ruin_50'] <= 1 and 0 <= r['ruin_20'] <= r['ruin_50']
    assert len(r['chart']['samples']) == 20 and r['n'] == 500
    json.dumps(lab._js(r))


def test_mc_bootstrap_and_risk_mode():
    tr, f = mc_trades()
    r = lab.monte_carlo(tr, start=500, n=400, method='bootstrap', seed=2)
    assert r['end_capital']['p5'] < r['end_capital']['p95']
    tr2 = pd.DataFrame(dict(R=np.r_[[2.0] * 30, [-1.0] * 30]))
    a = lab.monte_carlo(tr2, start=500, n=300, risk_per_trade=0.5, seed=0)
    assert a['mode'] == 'R*risk_per_trade' and a['ruin_20'] > 0.5               # 30 straight -50% losses possible
    r_again = lab.monte_carlo(tr, start=500, n=400, method='bootstrap', seed=2)
    assert r_again['end_capital'] == r['end_capital']                             # seeded
    with pytest.raises(ValueError): lab.monte_carlo(tr.iloc[:3], n=100)


def test_mc_daily_block():
    idx = pd.date_range('2024-01-01', periods=400 * 6, freq='4h')
    cv = pd.Series(500 * np.exp(np.cumsum(0.001 + 0.01 * np.sin(np.arange(len(idx)) / 7))), index=idx)
    r = lab.monte_carlo_daily(cv, start=500, n=300, seed=0)
    assert r['actual']['end'] == pytest.approx(cv.resample('1D').last().iloc[-1], abs=0.01)
    assert r['end_capital']['p5'] < r['end_capital']['p95'] and r['max_dd']['p5'] <= r['max_dd']['p95'] <= 0
    assert r == lab.monte_carlo_daily(cv, start=500, n=300, seed=0)


# ------------------------------------------------------------------ lookahead
def test_lookahead_flags_leaky_indicator(monkeypatch):
    orig = S.indicators

    def leaky_indicators(df):
        out = orig(df)
        if 'leak' not in out:
            out = out.copy(); out['leak'] = out.c.shift(-1)                      # tomorrow's close: lookahead
        return out

    def sig_leaky(d, p, ctx):
        return dict(le=d.leak > d.c, se=d.c < -1, lx=d.leak < d.c, sx=d.c < -1)

    monkeypatch.setattr(S, 'indicators', leaky_indicators)
    monkeypatch.setitem(S.STRATEGIES, 'leaky', dict(name='leaky', style='calm', fn=sig_leaky, sides='long', mgmt=dict(stop_atr=2)))
    raw = {s: d.iloc[:600] for s, d in toy_raw().items()}
    r = lab.lookahead_check(raw, keys=['leaky', 'ema_st'], n_points=5, seed=0)
    assert not r['ok']
    assert r['leaky_strategies'] == ['leaky'] and r['strategies']['leaky']['last_bar_mismatches'] > 0
    assert 'leak' in r['leaky_indicators'] and r['strategies']['ema_st']['ok']
    ex = r['strategies']['leaky']['examples'][0]
    assert ex['at_last_bar'] is True
    json.dumps(lab._js(r))


def test_lookahead_real_slice_clean():
    load_data = pytest.importorskip('load_data')
    raw = load_data.load(syms=['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT'])
    if len(raw) < 4: pytest.skip('real data not present')
    raw = {s: d.iloc[-700:] for s, d in raw.items()}
    r = lab.lookahead_check(raw, n_points=4, seed=1)
    assert r['ok'], (r['leaky_strategies'], r['leaky_indicators'])
    assert set(r['strategies']) == set(S.STRATEGIES) and r['regime']


# ------------------------------------------------------------------ validation + job
SL = [dict(key='ema_st', share=1, risk=0.01, max_pos=3, symbols='all')]


@pytest.mark.parametrize('kind,req,msg', [
    ('optimize', dict(sleeves=SL, space=[dict(path='sleeves[0].mgmt.foo', values=[1])]), 'unknown management'),
    ('optimize', dict(sleeves=SL, space=[dict(path='sleeves[0].__class__', values=[1])]), 'not allowed'),
    ('optimize', dict(sleeves=SL, space=[dict(path='sleeves[3].risk', values=[0.01])]), 'only 1 slots'),
    ('optimize', dict(sleeves=SL, space=[dict(path='sleeves[0].risk', values=[0.5])]), 'between'),
    ('optimize', dict(sleeves=SL, space=[dict(path='sleeves[0].params.k', values=['x'])]), 'number'),
    ('optimize', dict(sleeves=SL, n_trials=301, space=[dict(path='sleeves[0].risk', values=[0.01])]), 'number of trials'),
    ('optimize', dict(sleeves=SL, method='grid', space=[dict(path='sleeves[0].mgmt.stop_atr', values=dict(min=1, max=10, step=0.2)),
                                                        dict(path='sleeves[0].risk', values=[0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07])]), 'combinations'),
    ('walk_forward', dict(sleeves=SL, days=1500, train_days=60, test_days=14, step_days=14, space=[dict(path='sleeves[0].risk', values=[0.01])]), 'windows'),
    ('monte_carlo', dict(sleeves=SL, n=10001), 'simulations'),
    ('monte_carlo', dict(preset='nope'), 'unknown preset'),
    ('lookahead', dict(keys=['nope']), 'unknown strategy'),
    ('optimize', dict(sleeves=SL, run_options=dict(evil=1), space=[dict(path='sleeves[0].risk', values=[0.01])]), 'unknown backtest option'),
    ('bogus', {}, 'kind'),
])
def test_validation_rejects(kind, req, msg):
    with pytest.raises(ValueError, match=msg):
        lab.validate_lab_request(kind, req)


def test_validation_accepts_allowed_paths():
    paths = ['sleeves[0].risk', 'sleeves[0].share', 'sleeves[0].max_pos', 'sleeves[0].mgmt.stop_atr', 'sleeves[0].mgmt.trail_atr',
             'sleeves[0].mgmt.be_r', 'sleeves[0].mgmt.tp1_r', 'sleeves[0].mgmt.tp1_frac', 'sleeves[0].mgmt.tp_r', 'sleeves[0].mgmt.max_bars',
             'sleeves[0].mgmt.pyramid.n', 'sleeves[0].mgmt.dca.step_atr', 'sleeves[0].mgmt.runner.giveback', 'sleeves[0].params.k']
    for p in paths:
        v = [1] if p.endswith(('max_pos', 'max_bars', '.n', 'params.k')) else [0.5] if 'frac' in p or 'giveback' in p or 'share' in p else [0.01] if p.endswith('risk') else [2.0]
        q = lab.validate_lab_request('optimize', dict(sleeves=SL, space=[dict(path=p, values=v)]))
        assert q['space_size'] == 1
    q = lab.validate_lab_request('walk_forward', dict(preset='balanced', space=[dict(path='sleeves[0].risk', values=[0.01, 0.02])]))
    assert q['windows_est'] <= lab.MAX_WINDOWS and len(q['sleeves']) == 3


def test_run_lab_job_json(book):
    get_book = lambda q: book
    seen = []
    r = lab.run_lab_job('monte_carlo', dict(sleeves=[dict(key='ema_st', share=1, risk=0.02, max_pos=3, symbols='all')], n=200, days=200),
                        get_book, progress=lambda f, t: seen.append(f))
    assert r['kind'] == 'monte_carlo' and 'backtest' in r and 'cpu_s' in r and seen and seen[-1] == 1.0 and 'max_dd' in r['daily']
    json.dumps(r)
    r = lab.run_lab_job('optimize', dict(sleeves=SL, days=200, n_trials=2, min_trades=1, space=[dict(path='sleeves[0].mgmt.stop_atr', values=[2, 3])]), get_book)
    json.dumps(r)
    stop = lab.run_lab_job('optimize', dict(sleeves=SL, days=200, n_trials=2, space=[dict(path='sleeves[0].risk', values=[0.01, 0.02])]),
                           get_book, should_stop=lambda: True)
    assert stop['cancelled'] is True


def test_resolve_sleeves_keeps_v31_slot_options():
    import lab as L, backtest as B
    class FakeBook: syms = ['BTCUSDT', 'ETHUSDT']
    sl = [dict(key='ema_mom', share=1, risk=0.02, max_pos=2, symbols='all', when='bull', trail_entry={'dev_atr': 1, 'max_bars': 3},
               pump_guard={'max_candle_atr': 3})]
    out = L.resolve_sleeves(sl, FakeBook())
    assert out[0]['when'] == 'bull' and out[0]['trail_entry']['dev_atr'] == 1 and out[0]['pump_guard']['max_candle_atr'] == 3
