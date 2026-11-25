"""Verify opposite option-index moves, baseline and daily observability."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v210 as M

PLANTED_MUTANTS = [
    ('skip_prior_skew_stress', 'elevated & skew_lower', 'skew_lower', 'test_requires_prior_skew_stress'),
    ('ignore_dispersion_repricing', ' & vix_higher', '', 'test_requires_vix_to_rise'),
    ('use_index_before_buffer', 'pulse.set_axis(pulse.index + pd.Timedelta(days=2))', 'pulse', 'test_applies_availability_buffer'),
]


def sample(prior_skew=123., current_skew=116., prior_vix=20., current_vix=21.2):
    days = pd.bdate_range('2025-01-15', periods=32)
    data = {'SKEW': [110.] * 30 + [prior_skew, current_skew],
            'VIX': [19.] * 30 + [prior_vix, current_vix]}
    return pd.DataFrame(data, index=days)


def test_applies_availability_buffer():
    prices = sample()
    flags = M.asymmetric_risk_eases(prices)
    assert flags[flags].index.tolist() == [prices.index[-1] + pd.Timedelta(days=2)]


def test_requires_prior_skew_stress():
    assert not M.asymmetric_risk_eases(sample(prior_skew=117., current_skew=111.)).any()


def test_requires_vix_to_rise():
    assert not M.asymmetric_risk_eases(sample(current_vix=19.8)).any()


def test_missing_session_cannot_be_skipped():
    prices = sample()
    prices.iloc[10, 1] = float('nan')
    assert not M.asymmetric_risk_eases(prices).any()
