import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_breadth_candidate as B  # noqa: E402
import run_rank_breadth_reversal_candidate as R  # noqa: E402


def tape(n=300, seed=1):
    rng = np.random.default_rng(seed); c = 100 * np.exp(np.cumsum(rng.normal(0, .01, n)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC'),
                             o=o, h=np.maximum(o, c) * 1.01, l=np.minimum(o, c) * .99, c=c,
                             v=1000 * (1 + rng.random(n))))


def test_registry_and_management_are_frozen():
    st = S.STRATEGIES[R.KEY]
    assert st['research_only'] is True and st['tf'] == '4h' and st['sides'] == 'both'
    assert st['params'] == S.RBR4H and st['reentry_lock'] is True
    assert st['mgmt'] == dict(stop_atr=1.5, tp1_r=1.0, tp1_frac=.5, be_r=1.0, tp_r=2.0, max_bars=18)


def test_context_rank_and_breadth_are_prefix_causal():
    raw = {s: S.indicators(tape(seed=i + 1)) for i, s in enumerate(
        ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'BNBUSDT', 'ADAUSDT', 'LINKUSDT'])}
    full = S.build_context(raw); cut = 260
    part = S.build_context({s: d.iloc[:cut].copy() for s, d in raw.items()})
    for s in raw:
        for key in ('rank7', 'rank7_n', 'breadth7'):
            assert np.array_equal(np.asarray(part[s][key]), np.asarray(full[s][key])[:cut], equal_nan=True)


def test_literal_long_and_short_cells_require_rank_breadth_reversal():
    d = S.indicators(tape()); i = 250
    d.loc[:, 'v'] = 1000.0; d.loc[:, 'atr'] = 2.0
    n = pd.Series(10.0, index=d.index); rank = pd.Series(5.0, index=d.index)
    breadth = pd.Series(.5, index=d.index)
    rank.iloc[i], breadth.iloc[i] = 10, .2
    d.loc[i - 1, 'rsi2'], d.loc[i, 'rsi2'] = 20, 40
    d.loc[i, ['o', 'c']] = [98, 100]
    ctx = dict(rank7=rank, rank7_n=n, breadth7=breadth)
    assert bool(S.sig_rank_breadth_reversal_4h(d, S.RBR4H, ctx)['le'].iloc[i])
    rank.iloc[i], breadth.iloc[i] = 1, .8
    d.loc[i - 1, 'rsi2'], d.loc[i, 'rsi2'] = 80, 60
    d.loc[i, ['o', 'c']] = [102, 100]
    assert bool(S.sig_rank_breadth_reversal_4h(d, S.RBR4H, ctx)['se'].iloc[i])


def test_actual_funding_sign_and_boundaries():
    events = [(8 * R.HOUR_MS, .001), (16 * R.HOUR_MS, -.002)]
    marks = [(7 * R.HOUR_MS, 100.0), (15 * R.HOUR_MS, 110.0)]
    assert R.funding_cost('X', 1, 2, 8 * R.HOUR_MS, 16 * R.HOUR_MS, events, marks) == .2
    assert R.funding_cost('X', -1, 2, 8 * R.HOUR_MS, 17 * R.HOUR_MS, events, marks) == .24


def test_actual_funding_scope_restores_shared_engine():
    old = B.BT.FUND_PER_BAR
    with R.actual_funding_scope({}, {}): assert B.BT.FUND_PER_BAR == 0
    assert B.BT.FUND_PER_BAR == old
