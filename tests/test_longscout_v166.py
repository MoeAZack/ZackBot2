"""Synthetic checks for bid resilience and displayed ask control."""
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v166 as M

PLANTED_MUTANTS = [
    ('count_two_sided_book_growth', ' & ask_change.le(.05)', '', 'test_ask_growth_blocks_the_bid_signal'),
    ('count_one_sell_day', ' & sells.shift(fill_value=False)', '', 'test_selling_must_persist'),
    ('reenter_persistent_conditions', ' & ~state.shift(fill_value=False)', '', 'test_bid_growth_signals_only_once'),
]


def sample():
    return pd.Series([100., 100., 121., 148.]), pd.Series([100., 100., 101., 103.]), pd.Series([0., -.2, -.2, -.2])


def test_bid_growth_signals_only_once():
    bid, ask, imbalance = sample()
    event = M.resilient_bid(bid, ask, imbalance)
    assert event[event].index.tolist() == [2]
    pd.testing.assert_series_equal(M.resilient_bid(bid.iloc[:3], ask.iloc[:3], imbalance.iloc[:3]), event.iloc[:3])


def test_ask_growth_blocks_the_bid_signal():
    bid, _, imbalance = sample()
    assert not M.resilient_bid(bid, bid, imbalance).any()


def test_selling_must_persist():
    bid, ask, imbalance = sample()
    imbalance.iloc[1] = 0.
    assert not M.resilient_bid(bid.iloc[:3], ask.iloc[:3], imbalance.iloc[:3]).any()


def test_book_manifest_schema_round_trips_in_ascii(tmp_path, monkeypatch):
    fixture = tmp_path / 'manifest.json'
    monkeypatch.setenv('LONGSCOUT_BTC_BID_RESILIENCE', str(fixture))
    manifest = {
        'status': 'COMPLETE_WITH_EXPLICIT_MASKS',
        'fields': ['bid', 'ask'],
        'sample': '23:55 to 24:00 UTC matched 1 percent bid and ask depth',
        'days': [{'date': '2024-01-01', 'bid': '7', 'ask': '6'}],
    }
    raw = json.dumps(manifest).encode('ascii')
    fixture.write_bytes(raw)
    monkeypatch.setattr(M, 'BID_RESILIENCE_MANIFEST_SHA256', hashlib.sha256(raw).hexdigest())
    assert M.load_book().iloc[0].tolist() == [7, 6]
    manifest['sample'] = '23:55 to 24:00 UTC matched ?1 percent depth'
    invalid = json.dumps(manifest).encode('ascii')
    fixture.write_bytes(invalid)
    monkeypatch.setattr(M, 'BID_RESILIENCE_MANIFEST_SHA256', hashlib.sha256(invalid).hexdigest())
    with pytest.raises(ValueError, match='invalid BTC bid/ask input contract'):
        M.load_book()
