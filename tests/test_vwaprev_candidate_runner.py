"""tools/research/run_vwaprev_candidate.py - the bounded VWAPREV runtime candidate runner (Codex 6184268429).

Synthetic only: a temp candle cache in the app._get_candles CSV format, a temp git repo for the clean-tree check; no real
cache, store or data folder is read, no network, no app settings or credentials."""
import hashlib
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_vwaprev_candidate as R                                                           # noqa: E402
import splits as SP                                                                         # noqa: E402

H4 = R.TF_MS
WINDOW = '2025-03-03T00:00:00Z/2025-05-05T00:00:00Z'            # inside walk_forward[3]
LO, HI = (SP.parse_utc(x) for x in WINDOW.split('/'))
WARM_LO = LO - R.WARMUP_BARS * H4
SYMS = ['ETHUSDT', 'SOLUSDT']
TAIL = 40                                                        # candles cached AFTER END (the real cache runs to now)
HEAD = 12                                                        # candles cached before the warm-up start


def tape(seed, scale):
    """Fixed-seed oscillating 4h OHLCV with a real-looking quote volume, from before the warm-up to after END."""
    t = np.arange(WARM_LO - HEAD * H4, HI + TAIL * H4, H4, dtype=np.int64)
    n = len(t)
    rng = np.random.default_rng(seed)
    r = rng.standard_t(4, n) * 0.006
    c = scale * np.exp(np.cumsum(r) * 0.3 + 0.08 * np.sin(np.arange(n) / 4.0))
    o = np.r_[c[0], c[:-1]]
    w = np.abs(rng.normal(0, 0.004, n)) * c
    h, l = np.maximum(o, c) + w, np.minimum(o, c) - w
    v = np.round(1000.0 + 500 * rng.random(n), 3)
    qv = np.round((h + l + c) / 3 * v * (1 + rng.normal(0, 0.001, n)), 6)
    return pd.DataFrame(dict(t=t.astype(float), o=o, h=h, l=l, c=c, v=v, qv=qv))


def frames():
    return {s: tape(i + 1, 100.0 * (i + 1)) for i, s in enumerate(['BTCUSDT'] + SYMS)}


def write_cache(d, fr):
    os.makedirs(d, exist_ok=True)
    for s, df in fr.items():
        df.to_csv(os.path.join(d, f'{s}_4h.csv'), index=False)          # the app writes the same way (float t, ms)
    return d


def git_repo(path, dirty=False):
    os.makedirs(path, exist_ok=True)
    g = lambda *a: subprocess.run(['git', '-C', path, '-c', 'user.name=t', '-c', 'user.email=t@t', '-c',
                                   'commit.gpgsign=false', *a], check=True, capture_output=True)
    g('init', '-q')
    with open(os.path.join(path, 'a.txt'), 'w') as f:
        f.write('x\n')
    g('add', 'a.txt')
    g('commit', '-q', '-m', 'init')
    if dirty:
        with open(os.path.join(path, 'untracked.txt'), 'w') as f:
            f.write('y\n')
    return path


@pytest.fixture
def env(tmp_path):
    cache = write_cache(str(tmp_path / 'candles'), frames())
    repo = git_repo(str(tmp_path / 'repo'))
    return dict(cache=cache, repo=repo, tmp=tmp_path)


def go(env, out='p.json', **kw):
    kw = dict(dict(cache=env['cache'], window=WINDOW, symbols=SYMS, repo=env['repo']), **kw)
    return R.run(out=str(env['tmp'] / out), **kw)


# ------------------------------------------------------------------ window guards
@pytest.mark.parametrize('spec', [None, '', '  '])
def test_window_required(env, spec):
    with pytest.raises(R.RunnerError, match='explicit --window'):
        go(env, window=spec)
    assert not os.path.exists(env['tmp'] / 'p.json')


def test_cli_window_required_no_default(env):
    with pytest.raises(SystemExit) as e:
        R.main(['--cache', env['cache'], '--symbols', 'ETHUSDT', '--out', str(env['tmp'] / 'p.json')])
    assert e.value.code == 2


@pytest.mark.parametrize('spec', ['2026-06-01T00:00:00Z/2026-09-28T04:00:00Z', '2026-06-01T00:00:00Z/2026-10-12T00:00:00Z',
                                  '2026-09-28T00:00:00Z/2026-10-12T00:00:00Z'])
def test_holdout_end_refused(env, spec):
    with pytest.raises(R.RunnerError, match='sealed|holdout'):
        go(env, window=spec)


def test_end_on_cutoff_accepted():
    lo, hi, _ = R.check_window('2026-06-01T00:00:00Z/2026-09-28T00:00:00Z')
    assert R._utc(hi) == R.SEALED_CUTOFF


@pytest.mark.parametrize('spec', ['2020-12-07T00:00:00Z/2021-03-01T00:00:00Z', '2020-06-01T00:00:00Z/2020-12-01T00:00:00Z'])
def test_calibration_overlap_refused(env, spec):
    with pytest.raises(R.RunnerError, match='calibration'):
        go(env, window=spec)


def test_warmup_guard(env):
    with pytest.raises(R.RunnerError, match='warm-up'):         # rex_window accepts it; the 220-bar warm-up crosses 2021-01-04
        go(env, window='2021-01-11T00:00:00Z/2021-03-01T00:00:00Z')
    first = SP.parse_utc('2021-01-04T00:00:00Z') + R.WARMUP_BARS * H4
    lo, _, warm = R.check_window(f'{R._utc(first)}/2021-03-01T00:00:00Z')
    assert warm == SP.parse_utc('2021-01-04T00:00:00Z') and lo == first


# ------------------------------------------------------------------ clean tree
def test_dirty_tree_refused_before_cache_read(env, monkeypatch, tmp_path):
    repo = git_repo(str(tmp_path / 'dirty'), dirty=True)
    seen = []
    monkeypatch.setattr(R, 'read_cache', lambda *a: seen.append(a) or (_ for _ in ()).throw(AssertionError('read')))
    with pytest.raises(R.RunnerError, match='dirty git tree'):
        go(env, repo=repo)
    assert seen == [] and not os.path.exists(env['tmp'] / 'p.json')


# ------------------------------------------------------------------ bounded data
def test_candle_at_or_after_end_aborts(env, monkeypatch):
    real = R.slice_frame
    monkeypatch.setattr(R, 'slice_frame', lambda df, warm_lo, hi: real(df, warm_lo, hi + H4))    # a slicer that leaks END
    with pytest.raises(R.RunnerError, match='outside'):
        go(env)
    assert not os.path.exists(env['tmp'] / 'p.json') and not os.path.exists(str(env['tmp'] / 'p.json') + '.tmp')


def test_slice_before_indicators_post_end_spike_inert(env, tmp_path):
    base = go(env, out='a.json')
    fr = frames()
    for s, df in fr.items():                                      # wild candles strictly after END only
        post = df.t >= HI
        df.loc[post, ['o', 'h', 'l', 'c']] *= 50.0
        df.loc[post, ['v', 'qv']] *= 1e6
    cache2 = write_cache(str(tmp_path / 'candles2'), fr)
    spiked = go(env, out='b.json', cache=cache2)
    assert base['trades'] and base['trades'] == spiked['trades']
    assert base['stats'] == spiked['stats']
    assert all(base['cache']['files'][s]['sha256'] != spiked['cache']['files'][s]['sha256'] for s in base['cache']['files'])
    # the in-window signals themselves: indicators run on the sliced frame only
    for s in SYMS:
        a = R.slice_frame(R.read_cache(env['cache'], s)[0], WARM_LO, HI)
        b = R.slice_frame(R.read_cache(cache2, s)[0], WARM_LO, HI)
        pd.testing.assert_frame_equal(a, b)
        assert R._ms(a.t).max() == HI - H4 and R._ms(a.t).min() == WARM_LO


# ------------------------------------------------------------------ provenance
def test_provenance_fields_and_digests(env, tmp_path):
    fr = frames()
    eth = fr['ETHUSDT']
    i_win = np.flatnonzero((eth.t >= LO) & (eth.t < HI))[:3]
    i_warm = np.flatnonzero((eth.t >= WARM_LO) & (eth.t < LO))[:2]
    eth.loc[np.r_[i_win, i_warm], 'qv'] = np.nan
    eth.loc[eth.t >= HI, 'qv'] = np.nan                            # after END: never read, never counted
    cache = write_cache(str(tmp_path / 'c3'), fr)
    p = go(env, cache=cache)
    head = subprocess.run(['git', '-C', env['repo'], 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    assert p['commit'] == head and p['tree'] == 'clean'
    assert p['non_promotable'] is True and p['evidence_class'] == 'candidate-runtime' and p['format'] == R.FORMAT
    assert p['window'] == dict(start='2025-03-03T00:00:00Z', end='2025-05-05T00:00:00Z', end_exclusive=True,
                               bars=(HI - LO) // H4, guard='pit_run.rex_window', sealed_cutoff='2026-09-28T00:00:00Z')
    assert p['warmup'] == dict(bars=R.WARMUP_BARS, start=R._utc(WARM_LO), book_bars_before_start=R.WARMUP_BARS)
    assert p['universe']['requested'] == SYMS and p['universe']['run'] == SYMS
    assert p['universe']['book'] == sorted(['BTCUSDT'] + SYMS)
    for s in ['BTCUSDT'] + SYMS:
        with open(os.path.join(cache, f'{s}_4h.csv'), 'rb') as f:
            assert p['cache']['files'][s]['sha256'] == hashlib.sha256(f.read()).hexdigest()
        assert p['cache']['coverage'][s]['last'] == R._utc(HI - H4)
    assert p['nan_qv']['ETHUSDT'] == {'all': 5, 'in_window': 3, 'warmup': 2}
    assert p['nan_qv']['SOLUSDT'] == {'all': 0, 'in_window': 0, 'warmup': 0}
    assert p['strategy']['key'] == 'exp_vwaprev' and p['strategy']['params'] == dict(n=42, k=2.0, rg=1.0)
    assert p['strategy']['mgmt']['stop_atr'] == 1.5 and p['strategy']['mgmt']['max_bars'] == 30
    assert p['costs']['fee'] == 0.0005 and p['costs']['slip'] == 0.0002 and 'legacy' in p['costs']['exchange_rules']
    assert p['sleeve']['risk'] == 0.01 and p['sleeve']['max_pos'] == 4
    agg = p['stats']['aggregate']
    assert agg['trades'] == sum(p['stats']['per_symbol'][s]['trades'] for s in SYMS) == len(p['trades']) > 0
    assert all(LO <= SP.parse_utc(t['t_in']) < HI and SP.parse_utc(t['t_out']) < HI for t in p['trades'])
    body = {k: v for k, v in p.items() if k != 'packet_sha256'}
    assert p['packet_sha256'] == hashlib.sha256(R._canon(body)).hexdigest()
    with open(env['tmp'] / 'p.json', 'rb') as f:
        assert json.loads(f.read()) == p
    with pytest.raises(R.RunnerError, match='never overwritten'):
        go(env, cache=cache)


def test_frozen_universe_tier_and_symbols():
    st = R.frozen_universe()
    assert R.pick_symbols(st, 'CORE-10') == st['core_10'] and len(st['core_10']) == 10
    with pytest.raises(R.RunnerError, match='frozen universe'):
        R.pick_symbols(st, symbols=['FAKECOINUSDT'])
    with pytest.raises(R.RunnerError, match='exactly one'):
        R.pick_symbols(st, 'CORE-10', ['ETHUSDT'])


# ------------------------------------------------------------------ parity with the app backtest job
def test_parity_with_app_backtest(env, monkeypatch):
    import app as A
    sliced = {s: R.slice_frame(R.read_cache(env['cache'], s)[0], WARM_LO, HI) for s in ['BTCUSDT'] + SYMS}
    got = []
    real_run = A.BT.run
    monkeypatch.setattr(A.BT, 'run', lambda *a, **k: got.append(real_run(*a, **k)) or got[-1])
    monkeypatch.setattr(A, 'get_candles', lambda s, tf, days: sliced[s].copy())
    monkeypatch.setattr(A, 'save_json', lambda *a, **k: None)
    req = dict(tf='4h', days=(HI - LO) // 86_400_000, exchange_rules='off',
               sleeves=[dict(id='S1', key='exp_vwaprev', enabled=True, share=.25, risk=.01, max_pos=4, symbols=SYMS,
                             sides='both', mgmt={}, tf='4h')])
    jid = '20251015-000000-vwbr'
    A.JOBS[jid] = {}
    A.run_backtest_job(jid, req)
    job = A.JOBS.pop(jid)
    assert job['status'] == 'done', job.get('error')
    p = go(env)
    assert len(got) == 2
    (tr_app, cv_app), (tr_run, cv_run) = got
    assert len(tr_app) > 0
    pd.testing.assert_frame_equal(tr_app, tr_run)
    pd.testing.assert_series_equal(cv_app, cv_run)
    assert job['result']['stats'] == p['stats']['app']
    assert job['result']['by_symbol'] == {s: round(v['pnl'], 2) for s, v in sorted(
        p['stats']['per_symbol'].items(), key=lambda kv: round(kv[1]['pnl'], 2))}


def test_determinism(env):
    a, b = go(env, out='a.json'), go(env, out='b.json')
    with open(env['tmp'] / 'a.json', 'rb') as f, open(env['tmp'] / 'b.json', 'rb') as g:
        assert f.read() == g.read()
    assert a['packet_sha256'] == b['packet_sha256']
