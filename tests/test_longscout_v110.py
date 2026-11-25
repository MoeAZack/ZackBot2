"""Synthetic formula, causality, and asymmetric regime checks."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v110 as M

PLANTED_MUTANTS = [
    ('reverse_outcome_direction', '-direction * returns.shift()', 'direction * returns.shift()',
     'test_identifies_both_reversal_sides'),
    ('wrong_outcome_alignment', '-direction * returns.shift()', '-direction * returns.shift(2)',
     'test_unprofitable_streak_is_not_faded'),
    ('allow_trending_market', 'L1.regime_daily(close).eq(0)', 'L1.regime_daily(close).notna()',
     'test_prefix_gap_and_regime'),
]


def fixture(reversal=.01, direction=1):
    pattern = [reversal, -.01, -.01, -.01, reversal, .01, .01, -reversal, -.001, .001]
    r = np.tile(pattern, 14) * direction
    return pd.DataFrame(dict(t=pd.date_range('2026-01-01', periods=len(r), freq='D'), c=100*np.exp(np.cumsum(r))))


def test_identifies_both_reversal_sides(monkeypatch):
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(0., index=c.index))
    signals = M.sig_streak_fade(fixture(), {}, {})
    assert np.flatnonzero(signals['se']).tolist() == [126, 136]
    assert np.flatnonzero(signals['le']).tolist() == [133]


def test_unprofitable_streak_is_not_faded():
    d = fixture(reversal=.001).set_index('t')
    assert not M.streak_model(d.c, -1).any()
    assert not M.streak_model(d.c, 1).any()
    promising = fixture().set_index('t')
    assert M.streak_model(promising.c, -1).any()


def test_prefix_gap_and_regime(monkeypatch):
    d = fixture()
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(0., index=c.index))
    full = M.sig_streak_fade(d, {}, {})
    for cut in (123, 128, 134, 138):
        prefix = M.sig_streak_fade(d.iloc[:cut], {}, {})
        for key, flags in full.items():
            pd.testing.assert_series_equal(prefix[key], flags.iloc[:cut])
    gap = M.sig_streak_fade(d.drop(index=44), {}, {})
    assert not gap['le'].any() and not gap['se'].any()
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(1., index=c.index))
    trend = M.sig_streak_fade(d, {}, {})
    assert not trend['le'].any() and not trend['se'].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'both'
