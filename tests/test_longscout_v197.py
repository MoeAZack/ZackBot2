"""Synthetic tests of exact-vintage credit easing and publication timing."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v197 as M

PLANTED_MUTANTS = [
    ('ignore_risk_shift', ' & risk.abs().le(.020)', '', 'test_risk_stays_stable'),
    ('reverse_credit_direction', 'credit.le(-.020)', 'credit.ge(.020)', 'test_credit_must_ease'),
    ('ignore_release_buffer', 'published + pd.Timedelta(days=1)', 'published', 'test_use_dated_buffer'),
]


def sample():
    days = pd.date_range('2025-01-03', periods=3, freq='W-FRI')
    values = pd.DataFrame([[.13, .20, .12, .201], [.12, .21, .08, .205], [.01, .22, .05, .223]],
                          index=days, columns=M.FIELDS)
    return values, days + pd.Timedelta(days=6)


def test_risk_stays_stable():
    values, vintages = sample()
    values.iloc[1, 3] = .30
    assert not M.credit_eases(values, vintages).any()


def test_credit_must_ease():
    values, vintages = sample()
    assert M.credit_eases(values, vintages).tolist() == [False, True, False]


def test_use_dated_buffer():
    values, vintages = sample()
    event = M.credit_eases(values, vintages)
    assert event[event].index.tolist() == [vintages[1] + pd.Timedelta(days=1)]
