"""Research-only long after the BTC protocol improves subsidy per unit work.

Use end-of-day Bitcoin Core main-chain height to calculate the scheduled
block subsidy. Divide by end-of-day difficulty to obtain a proportional
subsidy-per-work proxy. Enter when the proxy rises at least 3% in one day
while the subsidy is unchanged. Use a seven-day lag for reconstructed chain
data. Require a rising target close and nonbear BTC and target regimes.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Uses changes to a protocol-set reward per unit expected work, excluding fee '
                  'income and fiat price. V139 measures recovery in observed hash activity, '
                  'which can fall while the retarget raises the base reward per unit work.')
RETARGET_REWARD_SHA256 = '0c58e2c1246e1f20c7386de4e77ea44e21ac7bfd3ff6e6650c77769b5e759cc9'
MARKET_FEATURE = ('RETARGET_REWARD_SHA256', 'BTC end-of-day block height and difficulty')
MARKET_FEATURE_SOURCE = 'Open Bitcoin Metrics release 0.1.0, Bitcoin Core full-node data'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_retarget_reward_20261125/manifest.json'
_BTC = None


def load_retarget():
    raw = Path(os.environ.get('LONGSCOUT_BTC_RETARGET_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != RETARGET_REWARD_SHA256:
        raise ValueError('expected pinned BTC retarget input')
    data = json.loads(raw)
    fields = ['height', 'difficulty']
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE', '1d', fields):
        raise ValueError('invalid BTC retarget input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('BTC retarget dates must be continuous and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields).apply(pd.to_numeric, errors='raise')


def work_reward_improves(height, difficulty):
    valid = np.isfinite(height) & height.ge(0) & height.eq(np.floor(height)) & np.isfinite(difficulty) & difficulty.gt(0)
    subsidy_epoch = (height.where(valid, 0) // 210_000).clip(upper=64).astype('int64')
    subsidy = pd.Series(np.right_shift(5_000_000_000, subsidy_epoch.to_numpy()), index=height.index).where(valid)
    subsidy = subsidy.where(subsidy.gt(0))
    relative_reward = subsidy / difficulty.where(valid)
    gain = relative_reward.pct_change(fill_method=None).ge(.03)
    return (gain & subsidy.eq(subsidy.shift()) & height.diff().gt(0)).shift(7, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    source = load_retarget()
    event = work_reward_improves(source.height, source.difficulty)
    _BTC = event.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_reward_change(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l152_btc_protocol_reward_retarget_1d': dict(
        name='[RESEARCH] L152 BTC protocol reward retarget long', style='momentum',
        fn=sig_reward_change, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
