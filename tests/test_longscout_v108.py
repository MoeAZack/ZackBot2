"""Synthetic point-in-time rank and BTC eligibility checks."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v108 as M

PLANTED_MUTANTS = [
    ('ignore_btc_entry_filter', ' & ret.gt(0) & btc_ok', ' & ret.gt(0)',
     'test_btc_gate_and_rank_hysteresis'),
    ('use_current_day_liquidity', 'volumes.shift().rolling(30', 'volumes.rolling(30',
     'test_liquidity_and_peer_count_fail_closed'),
    ('rank_lower_winners_first', 'rank(axis=1, method=', 'rank(axis=1, ascending=False, method=',
     'test_btc_gate_and_rank_hysteresis'),
]


def panel():
    days = np.arange(220)
    dates = pd.date_range('2024-01-01', periods=len(days), freq='D')
    def frame(slope):
        return pd.DataFrame(dict(t=dates, c=100 * np.exp(days * slope), qv=20e6))

    bars = {f'C{i}': frame(i * .0001 + .0001) for i in range(30)}
    bars['BTCUSDT'] = frame(.001)
    bars['C0'].loc[205:206, 'c'] = 140
    bars['C0'].loc[207, 'c'] = 95
    return bars


def evaluate(bars):
    M.set_panel(bars)
    return M.sig_rotation(bars['C0'], {}, {'research_symbol': 'C0'})


def test_btc_gate_and_rank_hysteresis():
    bars = panel()
    signals = evaluate(bars)
    assert signals['le'].iloc[205] and not signals['le'].iloc[206]
    assert not signals['lx'].iloc[206] and signals['lx'].iloc[207]
    assert M._RANK['C0'].iloc[205] == 1
    bars['BTCUSDT']['c'] = 100
    assert not evaluate(bars)['le'].any()
    assert evaluate(bars)['lx'].all()


def test_liquidity_and_peer_count_fail_closed():
    bars = panel()
    bars['C0'].loc[175:190, 'qv'] = 2e6
    assert not evaluate(bars)['le'].iloc[205]
    assert pd.isna(M._RANK['C0'].iloc[205])
    bars = panel()
    del bars['C1']
    assert not evaluate(bars)['le'].any()
    assert M._RANK['C0'].isna().all()


@pytest.mark.parametrize('cut', [202, 206, 207, 210])
def test_future_peers_and_btc_are_unavailable(cut):
    bars = panel()
    expected = evaluate(bars)
    prefix = evaluate({k: d.iloc[:cut] for k, d in bars.items()})
    for key, values in expected.items():
        pd.testing.assert_series_equal(prefix[key], values.iloc[:cut])


def test_default_off_and_symbol_context_is_required():
    bars = panel()
    M.set_panel(bars)
    with pytest.raises(ValueError, match='research_symbol'):
        M.sig_rotation(bars['C0'], {}, {})
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
