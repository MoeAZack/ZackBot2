"""Synthetic checks of reserve transfer and the historical release buffer."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v182 as M

PLANTED_MUTANTS = [
    ('allow_credit_expansion', ' & change.reserve_bank_credit.abs().le(20_000)', '', 'test_fed_credit_must_be_stable'),
    ('drop_treasury_drawdown', ' & change.treasury_general_account.le(-35_000)', '', 'test_needs_treasury_drawdown'),
    ('remove_release_delay', '+ pd.Timedelta(days=1)', '+ pd.Timedelta(days=0)', 'test_uses_release_availability'),
]


def sample():
    dates = pd.date_range('2025-11-12', periods=3, freq='W-WED')
    release = pd.DatetimeIndex(pd.to_datetime(['2025-11-13', '2025-11-20', '2025-11-28']))
    holdings = pd.DataFrame([[3_000_000, 500_000, 6_500_000], [3_000_000, 500_000, 6_500_000],
                             [3_040_000, 457_000, 6_495_000]], index=dates, columns=M.FIELDS)
    return holdings, release


def test_fed_credit_must_be_stable():
    values, release = sample()
    values.iloc[-1, -1] = 6_528_000
    assert not M.reserves_release(values, release).any()


def test_needs_treasury_drawdown():
    values, release = sample()
    values.iloc[-1, 1] = 490_000
    assert not M.reserves_release(values, release).any()


def test_uses_release_availability():
    values, release = sample()
    pulse = M.reserves_release(values, release)
    assert pulse[pulse].index.tolist() == [pd.Timestamp('2025-11-29')]
    assert not M.reserves_release(values.iloc[:-1], release[:-1]).any()
