"""Synthetic correlation formula, contemporaneous eligibility and timing."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v115 as M

PLANTED_MUTANTS = [
    ('include_target_in_benchmark', 'returns.rsub(total, axis=0).div(count - 1, axis=0)',
     'returns.mul(0).add(total / count, axis=0)', 'test_peer_correlation_matches_independent_numpy'),
    ('allow_too_few_peers', 'min_count=51', 'min_count=50',
     'test_benchmark_and_signal_correlation_have_separate_minima'),
    ('allow_repeat_entry_above_threshold', ' & _MEDIAN.shift().lt(.50)', '',
     'test_first_cross_and_regime_are_required'),
]


def panel():
    rng = np.random.default_rng(115)
    n, peers = 45, 52
    t = pd.date_range('2024-01-01', periods=n, freq='D')
    shared = rng.normal(0, .01, (n, 1))
    return pd.DataFrame(shared + rng.normal(0, .015, (n, peers)), index=t)


def test_peer_correlation_matches_independent_numpy():
    values = panel()
    history = values.iloc[-30:].to_numpy()
    peer_r = [np.corrcoef(history[:, i], np.delete(history, i, axis=1).mean(axis=1))[0, 1]
              for i in range(history.shape[1])]
    expected = np.median(peer_r)
    score = M.median_peer_correlation(values)
    assert score.iloc[-1] == pytest.approx(expected, abs=1e-12)
    assert score.iloc[:29].isna().all()
    pd.testing.assert_series_equal(score.iloc[:33], M.median_peer_correlation(values.iloc[:33]))


def test_benchmark_and_signal_correlation_have_separate_minima():
    result = M.median_peer_correlation(panel().iloc[:, :50])
    assert result.isna().all()


def test_first_cross_and_regime_are_required(monkeypatch):
    t = pd.date_range('2024-01-01', periods=28, freq='D')
    coin = pd.DataFrame(dict(t=t, c=np.exp(np.arange(len(t)) / 100), qv=2e7))
    score = np.full(len(t), .49)
    score[19:24] = .51
    monkeypatch.setattr(M, '_MEDIAN', pd.Series(score, index=t))
    monkeypatch.setattr(M, '_BTC', pd.Series(0., index=t))
    monkeypatch.setattr(M.L1, 'regime_daily', lambda close: pd.Series(0., index=close.index))
    assert np.flatnonzero(M.sig_peer_correlation(coin, {}, {})['le']).tolist() == [19]
    monkeypatch.setattr(M, '_BTC', pd.Series(-1., index=t))
    assert not M.sig_peer_correlation(coin, {}, {})['le'].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
