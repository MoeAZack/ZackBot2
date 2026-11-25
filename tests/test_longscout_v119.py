"""Synthetic checks for top-trader ratio normalization and causal fading."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v119 as M

PLANTED_MUTANTS = [
    ('ignore_account_count', ' - np.log(rates[ACCOUNT])', '',
     'test_position_share_and_number_of_accounts_are_distinct'),
    ('permit_missing_ratios', 'concentration.shift().rolling(45, min_periods=45)',
     'concentration.shift().rolling(45, min_periods=40)', 'test_causal_history_and_missing_ratios'),
    ('follow_instead_of_fade', ' & move.lt(0)', ' & move.gt(0)',
     'test_two_sides_need_incompatible_price_move'),
]


def fixture(monkeypatch):
    t = pd.date_range('2024-01-01', periods=65, freq='D')
    prices = pd.Series(100.0, index=t)
    prices.iloc[49:52] = [99, 100, 101]
    ratios = pd.DataFrame({M.ACCOUNT: 1.1, M.POSITION: 1.1}, index=t)
    ratios.loc[t[49], M.POSITION] = 1.8
    ratios.loc[t[51], M.POSITION] = .7
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(0., index=c.index))
    return prices, ratios


def test_position_share_and_number_of_accounts_are_distinct(monkeypatch):
    m, ratio = fixture(monkeypatch)
    assert M.market_conditions(m, ratio).short.iloc[49]
    ratio.iloc[49] = 1.8
    no_divergence = M.market_conditions(m, ratio)
    assert not no_divergence.short.iloc[49]


def test_two_sides_need_incompatible_price_move(monkeypatch):
    m, ratio = fixture(monkeypatch)
    signals = M.market_conditions(m, ratio)
    assert np.flatnonzero(signals.short).tolist() == [49]
    assert np.flatnonzero(signals.long).tolist() == [51]
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(-1., index=c.index))
    falling = M.market_conditions(m, ratio)
    assert not falling.short.any() and not falling.long.any()


def test_causal_history_and_missing_ratios(monkeypatch):
    m, ratio = fixture(monkeypatch)
    baseline = M.market_conditions(m, ratio)
    for stop in (45, 50, 52):
        pd.testing.assert_frame_equal(M.market_conditions(m.iloc[:stop], ratio.iloc[:stop]), baseline.iloc[:stop])
    ratio.iloc[30, 1] = np.nan
    missing = M.market_conditions(m, ratio)
    assert not missing.short.any() and not missing.long.any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'both'


def test_coin_signals_use_relative_returns(monkeypatch):
    btc, ratios = fixture(monkeypatch)
    frame = lambda prices: pd.DataFrame(dict(t=prices.index, c=prices.to_numpy(), qv=2e7))
    monkeypatch.setattr(M, 'load_ratios', lambda: ratios)
    M.set_btc(frame(btc))
    coin = pd.Series(105.0, index=btc.index)
    report = M.sig_position_fade(frame(coin), {}, {})
    assert np.flatnonzero(report['se']).tolist() == [49]
    assert np.flatnonzero(report['le']).tolist() == [51]
    common = M.sig_position_fade(frame(btc), {}, {})
    assert not common['se'].any() and not common['le'].any()
