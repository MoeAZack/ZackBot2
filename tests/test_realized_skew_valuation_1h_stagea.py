import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_realized_skew_valuation_candidate as R  # noqa: E402


def tape(returns):
    c = 100 * np.cumprod(1 + np.asarray(returns, float))
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=len(c), freq='1h', tz='UTC'),
                             o=c, h=c * 1.01, l=c * .99, c=c, v=1000.0))


def test_frozen_registry_and_management():
    st = S.STRATEGIES[R.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == dict(decision_hour=0, window=168, skew_threshold=.75, cost_multiple=5.0)
    assert st['mgmt'] == dict(stop_atr=2.0, tp_r=2.0, max_bars=24)


def test_skew_sides_are_mirrored_and_sampled_daily():
    n, i = 241, 240
    neg = np.tile([.001] * 23 + [-.02], 11)[:n]
    d = S.indicators(tape(neg)); d.loc[:, 'atr'] = 2.0
    sig = S.sig_realized_skew_valuation_1h(d, S.RSV1H, {})
    assert pd.Timestamp(d.t.iloc[i]).hour == 0
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])
    assert not bool(sig['le'].iloc[i - 1])
    pos = np.tile([-.001] * 23 + [.02], 11)[:n]
    d = S.indicators(tape(pos)); d.loc[:, 'atr'] = 2.0
    sig = S.sig_realized_skew_valuation_1h(d, S.RSV1H, {})
    assert bool(sig['se'].iloc[i]) and not bool(sig['le'].iloc[i])


def test_wrapper_restores_base_runner_globals():
    key, fmt = R.V.KEY, R.V.FORMAT
    assert R.KEY != key
    assert R.V.KEY == key and R.V.FORMAT == fmt
