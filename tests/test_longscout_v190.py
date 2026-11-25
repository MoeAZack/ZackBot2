"""Synthetic checks of option exposure, futures stability and release lag."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v190 as M

PLANTED_MUTANTS = [
    ('allow_futures_shock', ' & stable', '', 'test_futures_open_interest_must_stay_stable'),
    ('use_total_as_option_proxy', 'rows.combined_oi - futures', 'rows.combined_oi',
     'test_subtracts_futures_out_of_combined_exposure'),
    ('ignore_release_availability', 'return pulse.set_axis(available)', 'return pulse', 'test_delayed_release_is_used'),
]


def sample():
    report = pd.date_range('2025-09-23', periods=2, freq='W-TUE')
    published = pd.DatetimeIndex(pd.to_datetime(['2025-10-02', '2025-11-20']))
    return pd.DataFrame([[1000., 1150.], [1000., 1100.]], index=report, columns=M.FIELDS), published


def test_futures_open_interest_must_stay_stable():
    rows, published = sample()
    rows.iloc[1] = [1140., 1220.]
    assert not M.option_exposure_falls(rows, published).any()


def test_subtracts_futures_out_of_combined_exposure():
    rows, published = sample()
    assert M.option_exposure_falls(rows, published).tolist() == [False, True]


def test_delayed_release_is_used():
    rows, published = sample()
    event = M.option_exposure_falls(rows, published)
    assert event[event].index.tolist() == [published[-1]]
