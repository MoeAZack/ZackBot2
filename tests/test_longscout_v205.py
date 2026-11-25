"""Synthetic tests of Treasury auction composition, comparability and timing."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v205 as M

PLANTED_MUTANTS = [
    ('ignore_auction_size', ' & similar_size', '', 'test_excludes_offering_size_change'),
    ('ignore_broad_demand', ' & cover.ge(past_cover)', '', 'test_requires_at_least_typical_bid_cover'),
    ('use_auction_on_same_date', 'pulse.set_axis(pulse.index + pd.Timedelta(days=2))', 'pulse', 'test_applies_release_buffer'),
]


def sample(current=(40., 20., 40., 100., 2.9)):
    dates = pd.date_range('2024-01-04', periods=13, freq='7D')
    rows = [(50., 20., 30., 100., 2.8)] * 12 + [current]
    return pd.DataFrame(rows, index=dates, columns=M.FIELDS[:-1])


def test_applies_release_buffer():
    auctions = sample()
    signal = M.investor_share_pulse(auctions)
    assert signal[signal].index.tolist() == [auctions.index[-1] + pd.Timedelta(days=2)]


def test_excludes_offering_size_change():
    assert not M.investor_share_pulse(sample((60., 30., 60., 150., 2.9))).any()


def test_requires_at_least_typical_bid_cover():
    assert not M.investor_share_pulse(sample((40., 20., 40., 100., 2.5))).any()


def test_missing_week_breaks_history():
    auctions = sample().drop(sample().index[5])
    assert not M.investor_share_pulse(auctions).any()


def test_does_not_use_unreconciled_awards():
    assert not M.investor_share_pulse(sample((40., 20., 41., 100., 2.9))).any()
