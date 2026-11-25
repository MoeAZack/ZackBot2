"""Synthetic checks of ECB common-quote cancellation and causal recovery."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v177 as M

PLANTED_MUTANTS = [
    ('allow_cad_to_weaken', ' & cad.diff().gt(0)', '', 'test_both_currencies_must_strengthen'),
    ('allow_stale_fx', 'consecutive & weak', 'weak', 'test_stale_inputs_masked'),
    ('trade_before_buffer', 'pd.Timedelta(days=1)', 'pd.Timedelta(days=0)', 'test_release_buffer_and_quote_cancellation'),
]


def sample():
    risk = [1.50] * 5 + [1.53, 1.515]
    return pd.DataFrame(dict(AUD=risk, CAD=risk, CHF=1.0, JPY=150.),
                        index=pd.date_range('2026-01-05', periods=7, freq='B'))


def test_both_currencies_must_strengthen():
    quotes = sample()
    quotes.iloc[-1, :2] = [1.50, 1.535]
    assert not M.cyclicals_recover(quotes).any()


def test_stale_inputs_masked():
    quotes = sample()
    quotes.index = quotes.index[:5].append(quotes.index[5:] + pd.Timedelta(days=8))
    assert not M.cyclicals_recover(quotes).any()


def test_release_buffer_and_quote_cancellation():
    quotes = sample()
    event = M.cyclicals_recover(quotes)
    assert event[event].index.tolist() == [quotes.index[-1] + pd.Timedelta(days=1)]
    assert not M.cyclicals_recover(quotes.iloc[:-1]).any()
    rescaled = quotes.mul(pd.Series([1., .97, 1.02, .96, .99, 1.03, 1.02], index=quotes.index), axis=0)
    pd.testing.assert_series_equal(M.cyclicals_recover(rescaled), event)
