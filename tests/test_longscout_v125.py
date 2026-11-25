"""Synthetic same-contract annualization, gap and point-in-time tests."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v125 as M

PLANTED_MUTANTS = [
    ('fall_back_to_later_maturity', 'allocated |= active', 'allocated |= active & contracts[symbol].notna()',
     'test_does_not_fall_back_to_later_maturity'),
    ('ignore_basis_dislocation', ' & (reference - previous).ge(.05)', '',
     'test_cross_requires_a_deep_prior_discount'),
    ('use_wrong_direction', 'previous.lt(0)', 'previous.gt(0)',
     'test_cross_requires_a_deep_prior_discount'),
]


def fixture():
    date = pd.date_range('2024-01-01', periods=54, freq='D')
    spot = pd.Series(100., index=date)
    expiries = {'A': pd.Timestamp('2024-04-26 08:00'), 'B': pd.Timestamp('2024-05-31 08:00')}
    basis = pd.Series(.01, index=date)
    basis.iloc[39] = -.08
    maturity = lambda symbol, b: spot * np.exp(b * ((expiries[symbol] - (date + pd.Timedelta(days=1))) / pd.Timedelta(days=1)) / 365)
    contracts = {symbol: maturity(symbol, basis) for symbol in expiries}
    return spot, contracts, expiries, basis, maturity


def test_cross_requires_a_deep_prior_discount():
    spot, contracts, expiries, basis, price = fixture()
    flags = M.carry_signal(spot, contracts, expiries)
    assert np.flatnonzero(flags).tolist() == [40]
    basis.iloc[39] = -.02
    assert not M.carry_signal(spot, {'A': price('A', basis)}, {'A': expiries['A']}).any()


def test_does_not_fall_back_to_later_maturity():
    spot, contracts, expiries, _, _ = fixture()
    assert np.flatnonzero(M.carry_signal(spot, {'B': contracts['B']}, {'B': expiries['B']})).tolist() == [40]
    contracts['A'].iloc[40] = np.nan
    assert not M.carry_signal(spot, contracts, expiries).any()


def test_gap_and_future_prefix():
    spot, contracts, expiries, _, _ = fixture()
    full = M.carry_signal(spot, contracts, expiries)
    for cut in (20, 40, 41, 52):
        actual = M.carry_signal(spot.iloc[:cut], {s: c.iloc[:cut] for s, c in contracts.items()}, expiries)
        pd.testing.assert_series_equal(actual, full.iloc[:cut])
    spot.iloc[15] = np.nan
    assert not M.carry_signal(spot, contracts, expiries).any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
