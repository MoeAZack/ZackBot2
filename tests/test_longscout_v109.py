"""Synthetic checks for extrema order, completeness and close availability."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v109 as M

PLANTED_MUTANTS = [
    ('accept_high_before_low', ' & days.first_high.ge(12)', '',
     'test_same_daily_ohlc_with_reversed_path_rejects'),
    ('accept_incomplete_session', 'valid.groupby(day).sum().eq(6)', 'valid.groupby(day).sum().ge(5)',
     'test_gaps_and_invalid_candle_fail_closed'),
    ('open_before_session_closed', 't.dt.hour.eq(20) & day.map(hit)', 't.dt.hour.ne(23) & day.map(hit)',
     'test_signal_is_at_final_bar_only_and_prefix_causal'),
]


def fixture():
    t = pd.date_range('2026-01-01', periods=18, freq='4h')
    d = pd.DataFrame(dict(t=t, h=105., l=95., c=100.))
    d.loc[7, 'l'] = 90
    d.loc[10, 'h'] = 110
    d.loc[11, ['h', 'c']] = [109, 108]
    return d


def test_same_daily_ohlc_with_reversed_path_rejects():
    d = fixture()
    assert M.auction_recovery(d).iloc[11]
    d.loc[[6, 10], 'h'] = [110, 105]
    assert not M.auction_recovery(d).any()


def test_gaps_and_invalid_candle_fail_closed():
    d = fixture()
    assert not M.auction_recovery(d.drop(index=8)).any()
    assert not M.auction_recovery(d.drop(index=2)).any()
    d.loc[8, 'c'] = float('nan')
    assert not M.auction_recovery(d).any()


def test_signal_is_at_final_bar_only_and_prefix_causal(monkeypatch):
    d = fixture()
    monkeypatch.setattr(M.L1, 'coin_regime', lambda frame: np.zeros(len(frame)))
    signal = M.sig_auction_recovery(d, {}, {})['le']
    assert np.flatnonzero(signal).tolist() == [11]
    for cut in (6, 10, 11, 12, 16):
        partial = M.sig_auction_recovery(d.iloc[:cut], {}, {})['le']
        pd.testing.assert_series_equal(partial, signal.iloc[:cut])
    monkeypatch.setattr(M.L1, 'coin_regime', lambda frame: np.full(len(frame), -1.))
    assert not M.sig_auction_recovery(d, {}, {})['le'].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
