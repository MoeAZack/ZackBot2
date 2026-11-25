"""Synthetic side symmetry, timing and archived depth extraction checks."""
import io
from pathlib import Path
import sys
import zipfile

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research'), str(ROOT / 'research' / 'extra31_screen')]
import longscout_btc_depth_archive as archive
import longscout_v124 as M

PLANTED_MUTANTS = [
    ('allow_old_depth_imbalance', 'neutral & score.ge(.15)', 'score.ge(.15)',
     'test_neutral_transition_and_both_sides'),
    ('use_opposite_btc_return', 'score.le(-.15) & move.lt(0)', 'score.le(-.15) & move.gt(0)',
     'test_neutral_transition_and_both_sides'),
    ('count_falling_btc', 'L1.regime_daily(btc).isin((0., 1.))', 'L1.regime_daily(btc).notna()',
     'test_sideways_coin_and_nonbear_btc_required'),
]


def fixture(monkeypatch):
    t = pd.date_range('2024-01-01', periods=8, freq='D')
    price = pd.Series([100, 100, 101, 102, 102, 101, 100, 102], index=t)
    depth = pd.Series([0, .02, .18, .19, .1, -.18, -.32, 0], index=t)
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(0.0, index=c.index))
    return price, depth


def test_neutral_transition_and_both_sides(monkeypatch):
    price, depth = fixture(monkeypatch)
    flags = M.market_conditions(price, depth)
    assert np.flatnonzero(flags.long).tolist() == [2]
    assert np.flatnonzero(flags.short).tolist() == [5]
    for prefix in (2, 3, 6):
        pd.testing.assert_frame_equal(M.market_conditions(price.iloc[:prefix], depth.iloc[:prefix]), flags.iloc[:prefix])
    depth.iloc[4] = np.nan
    assert not M.market_conditions(price, depth).short.any()


def test_sideways_coin_and_nonbear_btc_required(monkeypatch):
    price, depth = fixture(monkeypatch)
    frame = lambda series: pd.DataFrame(dict(t=series.index, c=series.to_numpy(), qv=2e7))
    monkeypatch.setattr(M, 'load_depth', lambda: depth)
    M.set_btc(frame(price))
    coin = pd.Series([100, 100, 99, 99, 99, 101, 101, 101], index=price.index)
    report = M.sig_depth_fade(frame(coin), {}, {})
    assert np.flatnonzero(report['le']).tolist() == [2]
    assert np.flatnonzero(report['se']).tolist() == [5]
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(-1., index=c.index))
    assert not M.market_conditions(price, depth).long.any()
    assert not M.market_conditions(price, depth).short.any()


def test_archive_uses_inner_and_outer_bands_in_same_snapshot():
    day = '2024-01-01'
    values = [(-5, 100), (-1, 80), (1, 20), (5, 100)]
    rows = ['timestamp,percentage,depth,notional']
    rows.extend(f'{day} 23:55:01,{band},1,{notional}' for band, notional in values)
    rows.append(f'{day} 23:59:00,-1,1,9999')
    rows.append('2024-01-02 00:00:00,1,1,9999')
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as bundle:
        bundle.writestr('sample.csv', '\n'.join(rows))
    score, snapshots = archive.closing_depth(data.getvalue(), 'sample', day)
    assert snapshots == 1 and float(score) == pytest.approx(.6)
