"""Synthetic evidence for one-step forecast errors and band reentry."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v207 as M

PLANTED_MUTANTS = [
    ('include_current_forecast_error', 'calibration = error.shift().rolling', 'calibration = error.rolling',
     'test_requires_sixty_prior_forecasts'),
    ('omit_prior_breach', ' & outside_lower', '', 'test_no_fade_without_breach'),
    ('enter_after_crossing_center', ' & error.lt(0) & price.diff()', ' & price.diff()', 'test_does_not_enter_past_center'),
]


def flags(errors, prices):
    return M.reentry_flags(pd.Series(prices), pd.Series(errors),
                           pd.Series([-.02] * len(prices)), pd.Series([.02] * len(prices)))


def test_requires_sixty_prior_forecasts():
    close = pd.Series([100., 102., 99.] * 30)
    error, low, high = M.forecast_bounds(close)
    assert not error.iloc[:10].notna().any()
    assert low.first_valid_index() == high.first_valid_index() == 70
    changed = close.copy()
    changed.iloc[70] = 1.
    _, changed_low, changed_high = M.forecast_bounds(changed)
    assert changed_low.iloc[70] == low.iloc[70]
    assert changed_high.iloc[70] == high.iloc[70]


def test_buy_and_sell_after_reentry():
    buy, sell = flags([-.04, -.01, .04, .01], [96., 99., 104., 101.])
    assert buy.tolist() == [False, True, False, False]
    assert sell.tolist() == [False, False, False, True]


def test_no_fade_without_breach():
    buy, sell = flags([-.019, -.01], [98., 99.])
    assert not (buy | sell).any()


def test_does_not_enter_past_center():
    buy, sell = flags([-.04, .01], [96., 101.])
    assert not (buy | sell).any()


def test_missing_calibration_period_blocks_signals():
    close = pd.Series([100., 102., 99.] * 30)
    close.iloc[20] = float('nan')
    _, lower, upper = M.forecast_bounds(close)
    assert lower.isna().all() and upper.isna().all()
