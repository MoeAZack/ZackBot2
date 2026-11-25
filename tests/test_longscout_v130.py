"""Synthetic controls on liquidity membership, availability and confirmation."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v130 as M

PLANTED_MUTANTS = [
    ('ignore_peer_exits', 'entrants - exits', 'entrants',
     'test_market_net_subtracts_exits'),
    ('count_missing_as_illiquid', 'paired & liquid & ~was_liquid', 'liquid & ~was_liquid',
     'test_missing_prior_cannot_be_an_entrant'),
    ('ignore_target_direction', ' & price.gt(price.shift(10))', '',
     'test_positive_coin_and_btc_gate'),
]


def fixture():
    dates = pd.date_range('2024-01-01', periods=18, freq='D')
    return pd.DataFrame(5e6, index=dates, columns=range(65))


def test_market_net_subtracts_exits():
    med = fixture()
    med.iloc[:, 1] = 20e6
    med.iloc[8:, 1] = 5e6
    med.iloc[8:, 2:7] = 20e6
    result = M.liquid_net_change(med)
    assert result.iloc[8] == pytest.approx(4 / 65)
    assert result.iloc[15] == 0
    for cut in (7, 8, 9, 15):
        pd.testing.assert_series_equal(M.liquid_net_change(med.iloc[:cut]), result.iloc[:cut])


def test_missing_prior_cannot_be_an_entrant():
    med = fixture()
    med.iloc[7, 0] = np.nan
    med.iloc[8:, 0] = 20e6
    assert M.liquid_net_change(med).iloc[8] == 0
    assert M.liquid_net_change(med.iloc[:, :59]).isna().all()


def test_positive_coin_and_btc_gate(monkeypatch):
    t = fixture().index
    upward = pd.DataFrame(dict(t=t, c=100 + np.arange(len(t)), qv=2e7))
    downward = upward.assign(c=100 - np.arange(len(t)))
    market = pd.Series(False, index=t)
    market.iloc[11] = True
    monkeypatch.setattr(M, '_MARKET', market)
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(0.0, index=c.index))
    assert np.flatnonzero(M.sig_liquid_expansion(upward, {}, {})['le']).tolist() == [11]
    assert not M.sig_liquid_expansion(downward, {}, {})['le'].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
