"""Synthetic checks of two-token debt growth and availability."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v179 as M

PLANTED_MUTANTS = [
    ('allow_dai_stagnation', ' & relative.DAI_debt.ge(1.03)', '', 'test_each_debt_must_expand'),
    ('allow_liquidity_to_drop', ' & relative.DAI_available.between(.98, 1.12)', '',
     'test_available_liquidity_must_hold'),
    ('trade_before_availability', 'return event.shift(2, fill_value=False)', 'return event', 'test_two_day_buffer'),
]


def sample():
    return pd.DataFrame([[100., 100., 100., 100.]] * 8 + [[100., 104., 100., 104.]] * 5, columns=M.FIELDS)


def test_each_debt_must_expand():
    balances = sample()
    balances['DAI_debt'] = 100.
    assert not M.debt_expands(balances).any()


def test_available_liquidity_must_hold():
    balances = sample()
    balances.loc[8:, 'DAI_available'] = 96.
    assert not M.debt_expands(balances).any()


def test_two_day_buffer():
    balances = sample()
    events = M.debt_expands(balances)
    assert events[events].index.tolist() == [10]
    assert not M.debt_expands(balances.iloc[:10]).any()
