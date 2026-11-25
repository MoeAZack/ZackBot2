"""Synthetic evidence for open-interest ordering, masking and close timing."""
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v118 as M

PLANTED_MUTANTS = [
    ('ignore_oi_contraction', ' & oi_change.le(-.02)', '',
     'test_shock_requires_price_and_oi_and_today_recovery'),
    ('allow_oi_rebuilding', ' & oi_change.le(.01)', '',
     'test_shock_requires_price_and_oi_and_today_recovery'),
    ('ignore_historical_mask', 'oi_change = open_interest.reindex(btc.index).pct_change(fill_method=None)',
     'oi_change = open_interest.reindex(btc.index).ffill().pct_change(fill_method=None)',
     'test_missing_oi_cannot_trigger_and_future_does_not_leak'),
]


def fixture(monkeypatch):
    t = pd.date_range('2024-01-01', periods=7, freq='D')
    price = pd.Series([100, 97, 98, 100, 97, 98, 99], index=t)
    oi = pd.Series([100, 96, 96, 100, 100, 100, 100], index=t)
    monkeypatch.setattr(M.L1, 'regime_daily', lambda p: pd.Series(0.0, index=p.index))
    return price, oi


def test_shock_requires_price_and_oi_and_today_recovery(monkeypatch):
    price, oi = fixture(monkeypatch)
    hit = M.recovery_flags(price, oi)
    assert np.flatnonzero(hit).tolist() == [2]
    oi.iloc[2] = 98
    assert not M.recovery_flags(price, oi).any()


def test_missing_oi_cannot_trigger_and_future_does_not_leak(monkeypatch):
    price, oi = fixture(monkeypatch)
    reference = M.recovery_flags(price, oi)
    for cut in (1, 2, 3, 5):
        pd.testing.assert_series_equal(M.recovery_flags(price.iloc[:cut], oi.iloc[:cut]), reference.iloc[:cut])
    oi.iloc[2] = np.nan
    assert not M.recovery_flags(price, oi).any()


def test_signal_filters_out_coin_underperformance(monkeypatch):
    btc, oi = fixture(monkeypatch)
    frame = lambda prices: pd.DataFrame(dict(t=prices.index, c=prices.to_numpy(), qv=2e7))
    monkeypatch.setattr(M, 'load_open_interest', lambda: oi)
    M.set_btc(frame(btc))
    coin = btc.copy()
    coin.iloc[2] = 101
    assert np.flatnonzero(M.sig_oi_reset(frame(coin), {}, {})['le']).tolist() == [2]
    assert not M.sig_oi_reset(frame(btc), {}, {})['le'].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'


def test_manifest_hash_and_missing_day_are_enforced(monkeypatch, tmp_path):
    path = tmp_path / 'manifest.json'
    row = dict(status='COMPLETE_WITH_EXPLICIT_MASKS', field='sum_open_interest',
               sample='23:55 UTC exact observation',
               days=[dict(date='2026-01-01', sum_open_interest='42'), dict(date='2026-01-02', archive_missing=True)])
    path.write_text(json.dumps(row), encoding='utf-8')
    monkeypatch.setenv('LONGSCOUT_BTC_OI_MANIFEST', str(path))
    monkeypatch.setattr(M, 'BTC_OI_MANIFEST_SHA256', hashlib.sha256(path.read_bytes()).hexdigest())
    values = M.load_open_interest()
    assert values.iloc[0] == 42 and np.isnan(values.iloc[1])
    path.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='pinned BTC'):
        M.load_open_interest()
