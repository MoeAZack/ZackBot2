"""XAV-1H Stage A: distinct acceleration context, causal 4h regime and wrapper scope."""
import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_breadth_candidate as B  # noqa: E402
import run_xaccel_candidate as X  # noqa: E402


KEY = 'xaccel_volume_1h'


def tape(n=1100, drift=.0005):
    t = pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC')
    c = 100 * np.exp(np.cumsum(drift + .002*np.sin(np.arange(n)/7)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c)*1.002,
                             l=np.minimum(o, c)*.998, c=c, v=np.full(n, 1000.0)))


def test_frozen_research_only_registry_and_management():
    st = S.STRATEGIES[KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == S.XAV1H and st['reentry_lock'] is True
    assert st['mgmt'] == dict(stop_atr=1.5, tp1_r=1.0, tp1_frac=.5,
                               be_r=1.0, tp_r=2.0, max_bars=12)


def test_acceleration_context_is_not_existing_level_rank_and_is_prefix_causal():
    raw = {'BTCUSDT': tape(), 'ETHUSDT': tape(drift=.0002), 'SOLUSDT': tape(drift=-.0001)}
    dfs = {k: S.indicators(v.copy()) for k, v in raw.items()}
    ctx = S.build_context(dfs)
    for s in dfs:
        assert {'accel_rank', 'accel_value', 'accel_n', 'btc4_bull', 'btc4_bear'} <= set(ctx[s])
    assert not ctx['BTCUSDT']['accel_rank'].equals(ctx['BTCUSDT']['rank'])
    cut = 900
    prefix = {k: S.indicators(v.iloc[:cut].copy()) for k, v in raw.items()}
    before = S.build_context(prefix)
    for s in raw:
        for key in ('accel_rank', 'accel_value', 'accel_n', 'btc4_bull', 'btc4_bear'):
            assert np.array_equal(np.asarray(before[s][key]), np.asarray(ctx[s][key])[:cut], equal_nan=True), (s, key)


def test_literal_long_requires_new_rank_volume_trend_and_btc4_regime():
    d = S.indicators(tape(300))
    i = 260
    d.loc[:, 'e50'] = 101.0; d.loc[:, 'e200'] = 99.0; d.loc[:, 'ret42'] = .02; d.loc[:, 'atr'] = 1.0
    d.loc[:, 'c'] = 102.0; d.loc[:, 'v'] = 1000.0; d.loc[i, 'v'] = 2000.0
    rank = pd.Series(5.0, index=d.index); rank.loc[i] = 1.0
    accel = pd.Series(.5, index=d.index); n = pd.Series(10.0, index=d.index)
    bull = pd.Series(True, index=d.index); bear = pd.Series(False, index=d.index)
    ctx = dict(accel_rank=rank, accel_value=accel, accel_n=n, btc4_bull=bull, btc4_bear=bear)
    got = S.sig_xaccel_volume_1h(d, S.XAV1H, ctx)
    assert bool(got['le'].iloc[i])
    for name in ('not_new', 'volume', 'trend', 'regime'):
        x, c = d.copy(), {k: v.copy() for k, v in ctx.items()}
        if name == 'not_new': c['accel_rank'].loc[i-1] = 1.0
        if name == 'volume': x.loc[i, 'v'] = 1200.0
        if name == 'trend': x.loc[i, 'e50'] = 98.0
        if name == 'regime': c['btc4_bull'].loc[i] = False
        assert not bool(S.sig_xaccel_volume_1h(x, S.XAV1H, c)['le'].iloc[i]), name


def test_one_hour_wrapper_scope_restores_shared_runner_table():
    before = dict(B.TFS)
    assert '1h' not in before
    with X.one_hour_scope():
        assert B.TFS['1h'] == 3_600_000
    assert B.TFS == before
