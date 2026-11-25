"""Synthetic checks of paired ledger activity and source completion timing."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v193 as M

PLANTED_MUTANTS = [
    ('allow_one_token_activity', 'latest.ge(1.25 * typical).all(axis=1)', 'latest.ge(1.25 * typical).any(axis=1)',
     'test_both_transfer_counts_must_rise'),
    ('allow_supply_expansion', ' & stable_supply', '', 'test_native_supply_must_stay_stable'),
    ('use_unpublished_source', ' & pd.to_datetime(last_done, unit=\'s\').le(decision)', '',
     'test_checks_actual_completion')
]


def sample():
    days = pd.date_range('2025-01-01', periods=61)
    stamps = (days + pd.Timedelta(hours=27)).as_unit('s').asi8
    raw = {'usdc_eth_TxTfrCnt': [100.] * 45 + [150.] * 16, 'usdc_eth_SplyCur': 100.,
           'usdc_eth_AssetEODCompletionTime': stamps,
           'usdt_eth_TxTfrCnt': [100.] * 45 + [150.] * 16, 'usdt_eth_SplyCur': 100.,
           'usdt_eth_AssetEODCompletionTime': stamps}
    return pd.DataFrame(raw, index=days)


def test_both_transfer_counts_must_rise():
    data = sample()
    data.usdt_eth_TxTfrCnt = 100.
    assert not M.transfers_broaden(data).any()


def test_native_supply_must_stay_stable():
    data = sample()
    data.loc[data.index[45]:, 'usdt_eth_SplyCur'] = 100. + 4. * pd.Series(range(1, 17), index=data.index[45:])
    assert not M.transfers_broaden(data).any()


def test_checks_actual_completion():
    data = sample()
    flags = M.transfers_broaden(data)
    assert flags[flags].index.tolist() == [data.index[50]]
    data.loc[data.index[48], 'usdt_eth_AssetEODCompletionTime'] = data.index[53].timestamp()
    assert not M.transfers_broaden(data).any()
