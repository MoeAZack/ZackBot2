"""Tests for oil balance inputs and delayed publication dates."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v208 as M

PLANTED_MUTANTS = [
    ('skip_refinery_control', ' & refinery.diff().ge(0)', '', 'test_not_during_utilization_decline'),
    ('skip_two_earlier_draws', ' & earlier_draws', '', 'test_needs_two_inventory_draws'),
    ('ignore_release_calendar', 'released.loc[known] + pd.Timedelta(days=2)',
     'released.loc[known].index + pd.Timedelta(days=2)', 'test_uses_actual_release_date'),
]


def sample(stocks=(440., 433., 430., 434.), refinery=(95., 94., 91., 92.)):
    dates = pd.date_range('2022-06-03', periods=4, freq='W-FRI')
    released = pd.Series(pd.to_datetime(['2022-06-08', '2022-06-15', '2022-06-29', '2022-06-29']), index=dates)
    quantities = pd.DataFrame({M.FIELDS[1]: stocks, M.FIELDS[2]: refinery}, index=dates)
    return released, quantities


def test_uses_actual_release_date():
    flags = M.crude_inventory_rebuild(*sample())
    assert flags.index.is_unique
    assert flags[flags].index.tolist() == [pd.Timestamp('2022-07-01')]


def test_not_during_utilization_decline():
    assert not M.crude_inventory_rebuild(*sample(refinery=(95., 94., 91., 90.))).any()


def test_needs_two_inventory_draws():
    assert not M.crude_inventory_rebuild(*sample(stocks=(431., 433., 430., 434.))).any()


def test_missing_release_disqualifies_history():
    released, quantities = sample()
    released.iloc[1] = pd.NaT
    assert not M.crude_inventory_rebuild(released, quantities).any()
