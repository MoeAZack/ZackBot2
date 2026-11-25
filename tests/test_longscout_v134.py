"""Synthetic cross-asset IV rotation, market confirmation and gap checks."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v134 as M

PLANTED_MUTANTS = [
    ('allow_global_volatility_relief', ' & pair.btc_bvol_percent.diff().ge(0)', '',
     'test_ethereum_specific_change_required'),
    ('ignore_eth_outperformance', ' & er.gt(br)', '',
     'test_ethereum_specific_change_required'),
    ('fill_missing_paired_observations', 'pair = inputs.reindex(btc.index)', 'pair = inputs.reindex(btc.index).ffill()',
     'test_prefix_and_history_gap'),
]


def fixture():
    dates = pd.date_range('2024-01-01', periods=9, freq='D')
    btc = pd.Series(100 + np.arange(9), index=dates)
    pair = pd.DataFrame(dict(btc_bvol_percent=60., eth_bvol_percent=65., eth_perp_close=100.), index=dates)
    pair.iloc[4:, 1] = 53.
    pair.iloc[4:, 2] = 104.
    return btc, pair


def test_ethereum_specific_change_required():
    btc, pair = fixture()
    assert np.flatnonzero(M.rotation(btc, pair)).tolist() == [4]
    pair.iloc[4:, 0] = 59
    assert not M.rotation(btc, pair).any()
    pair.iloc[4:, 0] = 60
    pair.iloc[4:, 2] = 100.1
    assert not M.rotation(btc, pair).any()


def test_prefix_and_history_gap():
    btc, pair = fixture()
    signals = M.rotation(btc, pair)
    for i in (3, 4, 5, 8):
        pd.testing.assert_series_equal(M.rotation(btc.iloc[:i], pair.iloc[:i]), signals.iloc[:i])
    pair.iloc[3, 1] = np.nan
    assert not M.rotation(btc, pair).any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
