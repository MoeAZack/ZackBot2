"""Synthetic tests of the causal and paired flow-response estimates."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v171 as M

PLANTED_MUTANTS = [
    ('allow_missing_pair', 'spread = (buying - selling).where(paired & selling.ge(0))',
     'spread = (buying - selling).where(selling.ge(0))', 'test_needs_every_paired_bar'),
    ('allow_current_bar_in_numerator', 'product.where(sign, 0.).shift().rolling(60, min_periods=60)',
     'product.where(sign, 0.).rolling(60, min_periods=60)',
     'test_detects_first_crossing_after_bar_used'),
    ('allow_negative_sell_coefficient', 'paired & selling.ge(0)', 'paired', 'test_rejects_negative_sell_response'),
]


def sample():
    x = pd.Series(np.resize([.25, -.25], 100))
    slope = pd.Series(.006, index=x.index)
    slope.loc[70:] = .0165
    y = x * slope.where(x.gt(0), .006)
    return y, x


def test_detects_first_crossing_after_bar_used():
    y, x = sample()
    crossing = M.response_asymmetry(y, x)
    assert crossing[crossing].index.tolist() == [87]
    pd.testing.assert_series_equal(M.response_asymmetry(y.iloc[:87], x.iloc[:87]), crossing.iloc[:87])


def test_needs_every_paired_bar():
    y, x = sample()
    y.iloc[45] = np.nan
    assert not M.response_asymmetry(y, x).any()


def test_rejects_negative_sell_response():
    _, x = sample()
    coefficient = pd.Series(.006, index=x.index).mask(x.lt(0) & (x.index >= 70), -.30)
    assert not M.response_asymmetry(x * coefficient, x).any()
