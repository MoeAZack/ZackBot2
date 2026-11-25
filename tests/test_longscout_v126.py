"""Independent path labels, maturity and causal conditional range checks."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v126 as M

PLANTED_MUTANTS = [
    ('learn_outcomes_early', 'np.r_[np.full(6, np.nan), label]', 'np.r_[np.full(5, np.nan), label, np.nan]',
     'test_first_barrier_and_maturity_match_sequential_reference'),
    ('reverse_first_hit', 'first_lower - first_upper', 'first_upper - first_lower',
     'test_first_barrier_and_maturity_match_sequential_reference'),
    ('treat_unresolved_as_wins', 'cohort & observed.eq(-direction)', 'cohort & observed.ne(direction)',
     'test_unresolved_outcomes_cannot_make_a_win'),
]


def test_first_barrier_and_maturity_match_sequential_reference():
    closes = pd.Series([100., 101, 97, 104, 101, 98, 100, 100, 101, 100, 101, 100, 101])
    expected = np.full(len(closes), np.nan)
    for mature in range(6, len(closes)):
        begin = mature - 6
        base = closes.iloc[begin]
        expected[mature] = 0
        for later in closes.iloc[begin+1:begin+6]:
            if later >= base * 1.02:
                expected[mature] = 1
                break
            if later <= base * .98:
                expected[mature] = -1
                break
    assert expected[6] == -1
    np.testing.assert_array_equal(M.matured_outcomes(closes), expected)
    for end in range(1, len(closes) + 1):
        np.testing.assert_array_equal(M.matured_outcomes(closes.iloc[:end]), expected[:end])


def test_unresolved_outcomes_cannot_make_a_win():
    closes = pd.Series(np.tile([100., 98.7, 100., 100., 100.], 35))
    assert not M.side_odds(closes, -1).any()


def test_both_sides_and_nonbear_filter(monkeypatch):
    t = pd.date_range('2024-01-01', periods=175, freq='D')
    f = lambda closes: pd.DataFrame(dict(t=t, c=np.tile(closes, 35), qv=2e7))
    low = f([100., 97., 100., 100., 100.])
    high = f([100., 103., 100., 100., 100.])
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(0., index=c.index))
    M.set_btc(low)
    assert M.sig_first_passage(low, {}, {})['le'].iloc[171]
    assert M.sig_first_passage(high, {}, {})['se'].iloc[171]
    monkeypatch.setattr(M, '_BTC', pd.Series(False, index=t))
    assert not M.sig_first_passage(low, {}, {})['le'].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'both'
