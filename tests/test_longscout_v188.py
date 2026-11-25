"""Synthetic checks of directed daily forecasting and self-exclusion."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v188 as M

PLANTED_MUTANTS = [
    ('fit_current_label', '.corr(leader.shift()).shift()', '.corr(leader.shift())', 'test_fit_lags_the_latest_target_label'),
    ('accept_contemporaneous_peer', ' & (lag - same_day).ge(.10)', '', 'test_same_day_correlation_is_not_leadership'),
    ('allow_self_as_leader', '        rank[symbol] = np.nan\n', '', 'test_target_cannot_lead_itself'),
]


def observations():
    return pd.Series(np.random.default_rng(2026).standard_normal(130))


def test_fit_lags_the_latest_target_label():
    lead = observations()
    next_day = lead.shift().to_frame('target')
    strength = M.directed_strength(next_day, lead)
    assert strength.target.first_valid_index() == 61
    assert strength.target.iloc[61] > .99
    pd.testing.assert_frame_equal(M.directed_strength(next_day.iloc[:90], lead.iloc[:90]), strength.iloc[:90])
    changed = next_day.copy()
    changed.iloc[80, 0] = 1_000.
    assert M.directed_strength(changed, lead).target.iloc[80] == strength.target.iloc[80]


def test_same_day_correlation_is_not_leadership():
    lead = observations()
    twin = (lead + .50 * lead.shift()).to_frame('target')
    assert M.directed_strength(twin, lead).isna().all().all()


def test_target_cannot_lead_itself(monkeypatch):
    frame = pd.DataFrame({'A': [1., 2.], 'B': [3., 4.]})
    monkeypatch.setattr(M, 'directed_strength', lambda targets, leader: pd.DataFrame(.8, columns=targets.columns,
                                                                                   index=targets.index))
    result = M.select_leader_move(frame, ('A',))
    assert result.A.isna().all() and result.B.tolist() == [1., 2.]


def test_full_panel_preserves_native_target_index():
    rng = np.random.default_rng(188)
    days = pd.date_range('2025-01-01', periods=155)
    panel = {
        symbol: pd.DataFrame(dict(t=days, c=np.exp(rng.normal(0., .01, len(days)).cumsum()), qv=2e7))
        for symbol in ('BTCUSDT', *M.LEADERS, 'OTHERUSDT')
    }
    target = panel['OTHERUSDT'].iloc[14:].reset_index(drop=True)
    panel['OTHERUSDT'] = target
    M.set_panel(panel)
    flags = M.sig_directed_leadership(target, {}, {'research_symbol': 'OTHERUSDT'})
    assert set(flags) == {'le', 'se', 'lx', 'sx'}
    assert all(result.index.equals(target.index) for result in flags.values())
