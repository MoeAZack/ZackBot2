"""Synthetic controls for event buckets with frozen turnover budgets."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v112 as M

PLANTED_MUTANTS = [
    ('allow_slower_recovery', 'duration < span', 'duration > span',
     'test_complete_bucket_timing'),
    ('count_incomplete_turnover', 'if used < target:', 'if used < target / 2:',
     'test_complete_bucket_timing'),
    ('bridge_4h_gap', 'or not consecutive[i]', '',
     'test_gap_resets_open_bucket'),
]


def fixture():
    n = 62
    d = pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='4h'), qv=10., c=100.))
    # Buckets end on 47, 53, 59, and 60. The first supplies the price
    # anchor. The next two lose value. Bar 60 alone meets the final target.
    d.loc[53:58, 'c'] = 99
    d.loc[59, 'c'] = 98
    d.loc[60, ['qv', 'c']] = [60., 101.]
    return d


def test_complete_bucket_timing():
    d = fixture()
    assert np.flatnonzero(M.turnover_flags(d)).tolist() == [60]
    d.loc[60, 'qv'] = 50.
    assert np.flatnonzero(M.turnover_flags(d)).tolist() == [61]
    d.loc[61, 'c'] = 98.
    assert not M.turnover_flags(d).any()


def test_gap_resets_open_bucket():
    d = fixture()
    # The last bucket begins at 60 and needs bar 61 to close.
    d.loc[60, 'qv'] = 50.
    assert M.turnover_flags(d)[61]
    d.loc[61:, 't'] += pd.Timedelta(hours=4)
    assert not M.turnover_flags(d).any()


def test_prefix_causal_and_regime_gated(monkeypatch):
    d = fixture()
    monkeypatch.setattr(M.L1, 'coin_regime', lambda frame: np.zeros(len(frame)))
    full = M.sig_turnover_acceleration(d, {}, {})
    assert np.flatnonzero(full['le']).tolist() == [60]
    for cut in (42, 47, 54, 60, 61):
        partial = M.sig_turnover_acceleration(d.iloc[:cut], {}, {})
        for key, value in full.items():
            pd.testing.assert_series_equal(partial[key], value.iloc[:cut])
    monkeypatch.setattr(M.L1, 'coin_regime', lambda frame: np.full(len(frame), -1.))
    assert not M.sig_turnover_acceleration(d, {}, {})['le'].any()
    for key, value in M.CANDIDATES.items():
        assert value['research_only'] and value['sides'] == 'long' and key not in M.S.STRATEGIES
