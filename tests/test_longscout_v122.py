"""Synthetic spot versus perpetual market-share and eligibility checks."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v122 as M

PLANTED_MUTANTS = [
    ('allow_phantom_spot_growth', ' & spot.ge(1.25 * prior_spot)', '',
     'test_perpetual_turnover_collapse_does_not_count'),
    ('fill_missing_spot_bar', '    valid = np.isfinite(spot) & np.isfinite(perp)',
     '    spot = spot.ffill()\n    valid = np.isfinite(spot) & np.isfinite(perp)',
     'test_history_gap_is_unavailable'),
    ('allow_falling_coin', ' & np.isin(L1.coin_regime(d), (0., 1.))', '',
     'test_nonbear_coin_required'),
]


def fixture():
    t = pd.date_range('2024-01-01', periods=54, freq='4h')
    spot = pd.Series(100., index=t)
    perp = pd.Series(900., index=t)
    spot.iloc[48] = 200.
    return spot, perp


def test_perpetual_turnover_collapse_does_not_count():
    spot, perp = fixture()
    perp.iloc[46] = 200.
    assert np.flatnonzero(M.spot_share_accelerates(spot, perp)).tolist() == [48]


def test_history_gap_is_unavailable():
    spot, perp = fixture()
    result = M.spot_share_accelerates(spot, perp)
    for stop in (41, 49, 52):
        pd.testing.assert_series_equal(M.spot_share_accelerates(spot.iloc[:stop], perp.iloc[:stop]), result.iloc[:stop])
    spot.iloc[24] = np.nan
    assert not M.spot_share_accelerates(spot, perp).any()


def test_nonbear_coin_required(monkeypatch):
    spot, perp = fixture()
    c = 100 + np.arange(len(spot), dtype=float)
    btc = pd.DataFrame(dict(t=spot.index, c=c, qv=perp.to_numpy()))
    monkeypatch.setattr(M, 'load_spot_turnover', lambda: spot)
    monkeypatch.setattr(M.L1, 'coin_regime', lambda d: np.zeros(len(d), dtype=float))
    M.set_btc(btc)
    assert np.flatnonzero(M.sig_spot_lead(btc, {}, {})['le']).tolist() == [48]
    monkeypatch.setattr(M.L1, 'coin_regime', lambda d: np.full(len(d), -1.))
    assert not M.sig_spot_lead(btc, {}, {})['le'].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
