"""Independent reference and no-lookahead tests for weekday-free UTC slots."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v113 as M

PLANTED_MUTANTS = [
    ('include_current_return', 'sliding_window_view(returns[at], 30)[:-1]',
     'sliding_window_view(returns[at], 30)[1:]', 'test_prior_slot_matches_reference'),
    ('ignore_same_slot_gap', 'consecutive & np.isfinite(history)', 'np.isfinite(history)',
     'test_gaps_suppress_signals'),
    ('swap_fade_side', 'deviation <= -threshold', 'deviation >= threshold',
     'test_sides_regime_and_prefix'),
]


def fixture():
    dates = pd.date_range('2024-01-01', periods=38*6, freq='4h')
    # A normal 04:00 slot moves +3%; other slots have distinct means.
    r = np.resize([-.002, .030, .002, 0., -.001, .001], len(dates))
    r[34*6+1] = -.02
    r[35*6+4] = .04
    return pd.DataFrame(dict(t=dates, c=100*np.exp(np.cumsum(r))))


def test_prior_slot_matches_reference():
    d = fixture()
    for i in range(4, 170, 6):
        d.loc[i, 'c'] *= 1 + i / 10000
    d.loc[34*6+4, 'c'] *= 1.07
    deviation, limit = M.slot_deviations(d)
    i = 34*6+4
    rets = np.log(d.c/d.c.shift())
    history = np.array([rets.iloc[i - j*6] for j in range(1, 31)])
    med = np.median(history)
    mad = np.median(np.abs(history - med))
    assert deviation[i] == pytest.approx(rets.iloc[i] - med, abs=1e-12)
    assert limit[i] == pytest.approx(max(.025, 3*1.4826*mad))
    assert np.isnan(deviation[6])


def test_sides_regime_and_prefix(monkeypatch):
    d = fixture()
    monkeypatch.setattr(M.L1, 'coin_regime', lambda frame: np.zeros(len(frame)))
    full = M.sig_slot_fade(d, {}, {})
    assert full['le'].iloc[34*6+1]
    assert full['se'].iloc[35*6+4]
    assert not full['se'].iloc[(30*6+1):(34*6+1)].any()
    for cut in (181, 205, 206, 216):
        prefix = M.sig_slot_fade(d.iloc[:cut], {}, {})
        for key, flags in full.items():
            pd.testing.assert_series_equal(prefix[key], flags.iloc[:cut])
    monkeypatch.setattr(M.L1, 'coin_regime', lambda frame: np.ones(len(frame)))
    trending = M.sig_slot_fade(d, {}, {})
    assert not trending['le'].any() and not trending['se'].any()
    for key, value in M.CANDIDATES.items():
        assert value['research_only'] and value['sides'] == 'both' and key not in M.S.STRATEGIES


def test_gaps_suppress_signals():
    d = fixture()
    # Missing 04:00 from this day invalidates the later 04:00 window.
    d = d.drop(index=10*6+1)
    deviation, _ = M.slot_deviations(d)
    assert np.isnan(deviation[d.index.get_loc(34*6+1)])
