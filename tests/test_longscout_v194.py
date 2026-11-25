"""Synthetic checks of pageview controls and publication buffer."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v194 as M

PLANTED_MUTANTS = [
    ('allow_bitcoin_attention_shock', ' & normalized.Bitcoin.between(.85, 1.15)', '', 'test_btc_attention_stays_normal'),
    ('allow_whole_market_attention_shock', ' & normalized.Stock_market.between(.85, 1.15)', '',
     'test_market_attention_stays_normal'),
    ('ignore_pageview_buffer', 'return pulse.shift(2, fill_value=False)', 'return pulse', 'test_uses_delayed_first_event'),
]


def sample():
    dates = pd.date_range('2025-01-01', periods=80)
    view = pd.DataFrame(100., index=dates, columns=M.FIELDS)
    view.loc[dates[65:67], 'Ethereum'] = 175.
    return view


def test_btc_attention_stays_normal():
    counts = sample()
    counts.iloc[65:67, 1] = 150.
    assert not M.eth_attention_spikes(counts).any()


def test_market_attention_stays_normal():
    counts = sample()
    counts.iloc[65:67, 2] = 150.
    assert not M.eth_attention_spikes(counts).any()


def test_uses_delayed_first_event():
    counts = sample()
    flag = M.eth_attention_spikes(counts)
    assert flag[flag].index.tolist() == [counts.index[67]]
    assert not M.eth_attention_spikes(counts.iloc[:67]).any()


def test_same_weekday_controls_regular_traffic_pattern():
    counts = sample()
    counts['Ethereum'] = 100.
    counts.loc[counts.index.dayofweek == 0, 'Ethereum'] = 200.
    assert not M.eth_attention_spikes(counts).any()
