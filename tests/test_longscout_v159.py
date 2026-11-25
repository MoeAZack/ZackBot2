"""Synthetic checks for asynchronous labels and a price-confirmed recovery."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v159 as M

PLANTED_MUTANTS = [
    ('read_label_one_day_early', 'lag = position - 2', 'lag = position - 1', 'test_unpublished_label_cannot_change_fit'),
    ('train_on_unknown_labels', ' and np.isfinite(y[lag])', '', 'test_fit_freezes_after_last_observed_label'),
    ('buy_while_price_still_falling', ' & price_return.gt(0)', '', 'test_recovery_requires_rising_btc'),
]


def sample():
    x = pd.DataFrame({'flow': np.resize([0., 1., 2., 3.], 150)})
    y = x.flow * .8 + .2
    return x, y


def test_unpublished_label_cannot_change_fit():
    x, y = sample()
    base = M.forecast_pressure(x, y)
    y.iloc[130] += 15
    changed = M.forecast_pressure(x, y)
    pd.testing.assert_series_equal(base.iloc[:132], changed.iloc[:132])
    assert abs(base.iloc[132] - changed.iloc[132]) > .1


def test_fit_freezes_after_last_observed_label():
    x, y = sample()
    y.iloc[128:] = np.nan
    margin = M.forecast_pressure(x, y)
    assert margin.iloc[:121].isna().all()
    assert np.isfinite(margin.iloc[145:]).all()
    np.testing.assert_allclose(margin.iloc[132:136], margin.iloc[140:144])


def test_recovery_requires_rising_btc():
    margin = pd.Series([0.4, -.2, .3, -.4])
    price = pd.Series([-.01, -.02, -.01, .02])
    flag = M.pressure_eases(price, margin)
    assert flag[flag].index.tolist() == [3]
