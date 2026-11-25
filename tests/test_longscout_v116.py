"""Synthetic spectral reconstruction and point-in-time phase tests."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v116 as M

PLANTED_MUTANTS = [
    ('project_wrong_phase', 'rate * (WINDOW - 1)', 'rate * WINDOW',
     'test_reconstruct_known_projection'),
    ('omit_linear_detrending', ' - trend[:, None] * time', '',
     'test_reconstruct_known_projection'),
    ('allow_momentum_instead_of_fade', ' & (slope > 0)', ' & (slope < 0)',
     'test_two_sided_turns_and_regime'),
]


def fixture():
    tick = np.arange(150)
    wave = .14*np.cos(tick * (2*np.pi/24))
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=len(tick), freq='D'),
                             c=np.exp(2 + .004*tick + wave), qv=2e7))


def test_reconstruct_known_projection():
    d = fixture()
    share, phase, slope = M.cycle_features(d.c)
    window = np.log(d.c.iloc[8:104].to_numpy())
    t = np.arange(96)
    line = np.column_stack([np.ones(len(t)), t])
    residual = window - line @ np.linalg.lstsq(line, window, rcond=None)[0]
    omega = 2*np.pi/24
    wave = np.column_stack([np.cos(t*omega), np.sin(t*omega)])
    a, b = np.linalg.lstsq(wave, residual, rcond=None)[0]
    amp = np.hypot(a, b)
    projected = lambda step: (a*np.cos(step*omega) + b*np.sin(step*omega))/amp
    assert share[103] == pytest.approx(amp**2/2/np.var(residual))
    assert phase[103] == pytest.approx(projected(95))
    assert slope[103] == pytest.approx(projected(96) - projected(95))
    assert np.isnan(share[:95]).all()
    assert np.isnan(M.cycle_features(pd.Series(np.ones(120)))[0]).all()


def test_two_sided_turns_and_regime(monkeypatch):
    d = fixture()
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(0., index=c.index))
    M.set_btc(d)
    full = M.sig_cycle_fade(d, {}, {})
    assert full['le'].iloc[109] and full['se'].iloc[121]
    assert not full['le'].iloc[108] and not full['se'].iloc[120]
    M.set_btc(d.iloc[:123])
    part = M.sig_cycle_fade(d.iloc[:123], {}, {})
    for key, value in full.items():
        pd.testing.assert_series_equal(part[key], value.iloc[:123])
    monkeypatch.setattr(M, '_BTC', pd.Series(1., index=pd.DatetimeIndex(d.t)))
    trend = M.sig_cycle_fade(d, {}, {})
    assert not trend['le'].any() and not trend['se'].any()


def test_gap_cannot_form_a_complete_window(monkeypatch):
    d = fixture()
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(0., index=c.index))
    M.set_btc(d)
    sig = M.sig_cycle_fade(d.drop(index=50), {}, {})
    assert not sig['le'].loc[:145].any() and not sig['se'].loc[:145].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'both'
