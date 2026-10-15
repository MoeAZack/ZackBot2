"""tools/research/run_breadth_candidate.py - bounded breadth candidate runner (key / side / mgmt; INCOMPLETE symbols).

Synthetic only: the temp candle cache + temp git repo fixtures of test_vwaprev_candidate_runner; no real cache, store or
data folder is read, no network, no app settings or credentials."""
import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_breadth_candidate as B                                                           # noqa: E402
import test_vwaprev_candidate_runner as VT                                                  # noqa: E402
import backtest as BT                                                                       # noqa: E402

KEY = 'exp_range'


@pytest.fixture
def env(tmp_path):
    cache = VT.write_cache(str(tmp_path / 'candles'), VT.frames())
    repo = VT.git_repo(str(tmp_path / 'repo'))
    return dict(cache=cache, repo=repo, tmp=tmp_path)


def go(env, out='p.json', **kw):
    kw = dict(dict(cache=env['cache'], window=VT.WINDOW, symbols=VT.SYMS, repo=env['repo'], key=KEY, side='long'), **kw)
    return B.run(out=str(env['tmp'] / out), **kw)


# ------------------------------------------------------------------ window / holdout / arguments
@pytest.mark.parametrize('spec', ['2026-06-01T00:00:00Z/2026-09-28T04:00:00Z', '2026-09-28T00:00:00Z/2026-10-12T00:00:00Z'])
def test_holdout_refused(env, spec):
    with pytest.raises(B.RunnerError, match='sealed|holdout'):
        go(env, window=spec)
    assert not os.path.exists(env['tmp'] / 'p.json')


def test_window_required_and_warmup_guard(env):
    with pytest.raises(B.RunnerError, match='explicit --window'):
        go(env, window=None)
    with pytest.raises(B.RunnerError, match='warm-up'):
        go(env, window='2021-01-11T00:00:00Z/2021-03-01T00:00:00Z')


def test_non_4h_key_blocked(env):
    with pytest.raises(B.RunnerError, match='BLOCKED: no 15m cache'):
        go(env, key='exp_scalp')


def test_dirty_tree_refused(env, tmp_path):
    with pytest.raises(B.RunnerError, match='dirty git tree'):
        go(env, repo=VT.git_repo(str(tmp_path / 'dirty'), dirty=True))


def test_mgmt_arguments():
    assert B.build_mgmt('defaults') == {}
    with pytest.raises(B.RunnerError):
        B.build_mgmt('profit_capture')                    # no invented preset
    with pytest.raises(B.RunnerError):
        B.build_mgmt('custom', {})
    with pytest.raises(B.RunnerError, match='both'):
        B.build_mgmt('custom', {'tp1_r': 1.0})
    with pytest.raises(B.RunnerError, match='outside'):
        B.build_mgmt('custom', {'max_bars': 0})
    assert B.build_mgmt('custom', {'be_r': 1, 'max_bars': 18.0}) == {'be_r': 1.0, 'max_bars': 18}


# ------------------------------------------------------------------ side filter
def test_side_filter_filters(env):
    lg = go(env, out='l.json', side='long')
    sh = go(env, out='s.json', side='short')
    assert lg['trades'] and sh['trades']
    assert {t['side'] for t in lg['trades']} == {1} and {t['side'] for t in sh['trades']} == {-1}
    assert lg['sleeve']['sides'] == 'long' and sh['sleeve']['sides'] == 'short'
    assert lg['status'] == sh['status'] == 'ok'


def test_never_emitted_side_recorded(env):
    p = go(env, key='bear_breakdown', side='long')       # signal function designs no longs
    assert p['status'] == 'not emitted' and p['raw_entry_signals'] == 0 and p['trades'] == []


# ------------------------------------------------------------------ management variant
def test_custom_mgmt_differs_from_defaults(env):
    d = go(env, out='d.json')
    c = go(env, out='c.json', mgmt_mode='custom', custom=dict(tp1_r=0.5, tp1_frac=0.5, be_r=0.5, max_bars=18))
    assert d['strategy']['mgmt_override'] == {} and d['strategy']['mgmt'] == {'stop_atr': 1.5, 'max_bars': 30}
    assert c['strategy']['mgmt'] == {'stop_atr': 1.5, 'max_bars': 18, 'tp1_r': 0.5, 'tp1_frac': 0.5, 'be_r': 0.5}
    assert d['trades'] != c['trades'] and d['stats']['aggregate'] != c['stats']['aggregate']


# ------------------------------------------------------------------ completeness
def test_gap_symbol_incomplete_and_excluded(env, tmp_path):
    fr = VT.frames()
    sol = fr['SOLUSDT']
    gap = np.flatnonzero((sol.t >= VT.LO) & (sol.t < VT.HI))[50:53]
    fr['SOLUSDT'] = sol.drop(index=gap).reset_index(drop=True)
    cache = VT.write_cache(str(tmp_path / 'gap'), fr)
    p = go(env, cache=cache)
    inc = p['universe']['incomplete']
    assert [x['symbol'] for x in inc] == ['SOLUSDT'] and inc[0]['missing'] == 3 and inc[0]['status'] == 'INCOMPLETE'
    assert p['universe']['run'] == ['ETHUSDT'] and 'SOLUSDT' not in p['universe']['book']
    assert all(t['symbol'] == 'ETHUSDT' for t in p['trades'])
    assert set(p['stats']['per_symbol']) == {'ETHUSDT'}
    assert p['stats']['aggregate']['trades'] == p['stats']['per_symbol']['ETHUSDT']['trades'] == len(p['trades'])


def test_btc_incomplete_refused(env, tmp_path):
    fr = VT.frames()
    fr['BTCUSDT'] = fr['BTCUSDT'].drop(index=[300]).reset_index(drop=True)
    with pytest.raises(B.RunnerError, match='BTCUSDT is INCOMPLETE'):
        go(env, cache=VT.write_cache(str(tmp_path / 'bg'), fr))


# ------------------------------------------------------------------ instrumentation does not change results; accounting
def test_probe_is_read_only_and_accounting_closes(env):
    p = go(env)
    raw = {s: VT.R.slice_frame(VT.R.read_cache(env['cache'], s)[0], VT.WARM_LO, VT.HI) for s in ['BTCUSDT'] + VT.SYMS}
    book = BT.Book(raw)
    n0 = int((B._ms(book.t) < VT.LO).sum())
    tr, _ = BT.run(book, [B.sleeve(KEY, 'long', VT.SYMS, {}, 0.01, 4)], start=500.0, max_lev=10.0, daily_halt=0.08,
                   warmup=n0, t0=np.datetime64(VT.LO, 'ms'), t1=np.datetime64(VT.HI, 'ms'),
                   fund_per_bar=BT.FUND_PER_BAR, exchange_rules=None)
    assert BT.FEE == 0.0005 and isinstance(BT.FEE, float)                    # restored
    assert [B._num(x) for x in tr.pnl] == [t['pnl'] for t in p['trades']]
    for t in p['trades']:
        assert abs(t['gross'] - t['fees'] - t['funding'] - t['pnl']) < 1e-5
        assert t['fees'] > 0 and t['funding'] >= -1e-6 and t['mfe_r'] >= 0 and t['mae_r'] >= 0
        assert t['regime'] in ('btc_above_ema200', 'btc_below_ema200')
    agg = p['stats']['aggregate']
    assert agg['trades'] == sum(r['trades'] for r in p['stats']['regime'].values())


# ------------------------------------------------------------------ determinism / never overwritten
def test_deterministic_rerun_and_no_overwrite(env):
    a = go(env, out='a.json')
    b = go(env, out='b.json')
    assert a['packet_sha256'] == b['packet_sha256']
    with open(env['tmp'] / 'a.json', 'rb') as f:
        assert json.loads(f.read()) == a
    with pytest.raises(B.RunnerError, match='never overwritten'):
        go(env, out='a.json')
    assert a['non_promotable'] is True and a['runner_sha256']['run_breadth_candidate.py']
