"""Synthetic cadence event timing, gaps and range direction tests."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v123 as M

PLANTED_MUTANTS = [
    ('see_payment_at_close', "side='left'", "side='right'", 'test_one_new_payment_is_visible_after_its_timestamp'),
    ('ignore_timestamp_gap', ' & confirmed & fresh', ' & fresh', 'test_gap_does_not_confirm_shorter_interval'),
    ('reverse_direction_of_fade', 'displacement <= -.01', 'displacement >= .01', 'test_nonbear_fades_the_move'),
]


def funding(times=(0, 8, 12, 16), intervals=(8, 8, 4, 4)):
    return pd.DataFrame(dict(calc_time=np.asarray(times)*M.HOUR_MS, funding_interval_hours=intervals))


def test_one_new_payment_is_visible_after_its_timestamp():
    close = np.array([8, 12, 16, 20, 24])*M.HOUR_MS
    expected = [False, False, True, False, False]
    flags = M.cadence_change(close, funding())
    assert flags.tolist() == expected
    for cut in range(1, len(close) + 1):
        assert M.cadence_change(close[:cut], funding()).tolist() == expected[:cut]


def test_gap_does_not_confirm_shorter_interval():
    close = np.array([8, 12, 16, 20, 24])*M.HOUR_MS
    assert not M.cadence_change(close, funding(times=(0, 8, 16, 20))).any()
    assert M.cadence_change(close, funding(intervals=(8, 8, 8, 4))).tolist() == [False, False, False, True, False]


def test_nonbear_fades_the_move(monkeypatch):
    t = pd.date_range('1970-01-01', periods=7, freq='4h')
    d = pd.DataFrame(dict(t=t, c=[100., 100., 99., 97., 98., 100., 102.]))
    monkeypatch.setattr(M.FUND, 'load_funding', lambda symbol: funding())
    monkeypatch.setattr(M.L1, 'coin_regime', lambda frame: np.zeros(len(frame)))
    monkeypatch.setattr(M.L1, 'btc_regime', lambda frame: np.ones(len(frame)))
    out = M.sig_funding_cadence(d, {}, {'research_symbol': 'SYNTHETIC'})
    assert np.flatnonzero(out['le']).tolist() == [3] and not out['se'].any()
    monkeypatch.setattr(M.L1, 'btc_regime', lambda frame: np.full(len(frame), -1.))
    assert not M.sig_funding_cadence(d, {}, {'research_symbol': 'SYNTHETIC'})['le'].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'both'
