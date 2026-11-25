"""Research-only long after estimated BTC mining revenue per hash recovers.

Compare each SHA-256 chain's native fees plus issuance in USD per unit of
estimated hashrate. Require the log BTC/BCH spread to recover from .10
below its past 60-day median to within .02 below. BTC's own estimate must
rise at least 5% and BCH's stay within a 5% log band. Exclude both event
days from the baseline. Wait two extra UTC days and check all recorded
completion times. Require nonbear BTC and an up-day nonbear target.
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

FAMILY_NOVELTY = ('Compares realized mining-revenue estimates per hash in two SHA-256 chains. '
                  'V139 uses BTC work without compensation; V152 uses the scheduled BTC '
                  'subsidy and V169/V172 use BTC pool concentration.')
SHA256_MINER_REVENUE_SHA256 = '8f11a00ef6b42b96cac30413c4374a4bacac8c726b7191e697d0eaaf427ab0c2'
MARKET_FEATURE = ('SHA256_MINER_REVENUE_SHA256', 'BTC and BCH fees, issuance, estimated hashrate, and USD price')
MARKET_FEATURE_SOURCE = 'Coin Metrics Community BTC and BCH network metrics and PriceUSD'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_sha256_mining_revenue_20261125/manifest.json'
ASSETS = ['btc', 'bch']
METRICS = ['HashRate', 'FeeTotNtv', 'IssTotNtv', 'AssetEODCompletionTime', 'PriceUSD']
FIELDS = [asset + '_' + metric for asset in ASSETS for metric in METRICS]
_BTC = None


def load_mining_revenue():
    raw = Path(os.environ.get('LONGSCOUT_SHA256_MINER_REVENUE', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SHA256_MINER_REVENUE_SHA256:
        raise ValueError('expected pinned BTC and BCH mining observations')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['assets'], data['fields']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', ASSETS, FIELDS,
    ):
        raise ValueError('invalid BTC and BCH mining output archive')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('mining revenue dates must be unique and consecutive')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')


def hash_revenue(data, asset):
    hashrate = data[asset + '_HashRate']
    fees = data[asset + '_FeeTotNtv']
    issuance = data[asset + '_IssTotNtv']
    price = data[asset + '_PriceUSD']
    good = hashrate.gt(0) & fees.ge(0) & issuance.ge(0) & (fees + issuance).gt(0) & price.gt(0)
    return np.log(((fees + issuance) * price / hashrate).where(good))


def mining_spread_recovers(btc, bch, completion):
    valid = np.isfinite(btc) & np.isfinite(bch) & np.isfinite(completion) & completion.gt(0)
    a, b = btc.where(valid), bch.where(valid)
    spread = a - b
    middle = spread.shift(2).rolling(60, min_periods=60).median()
    relief = ((spread.shift() - middle).le(-.10) & (spread - middle).ge(-.02)
              & a.diff().ge(np.log(1.05)) & b.diff().abs().le(np.log(1.05)))
    ready = completion.where(valid).rolling(62, min_periods=62).max().shift(2)
    decision = pd.Series(spread.index + pd.Timedelta(days=1), index=spread.index)
    return relief.shift(2, fill_value=False) & pd.to_datetime(ready, unit='s').le(decision)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    data = load_mining_revenue().where(lambda values: np.isfinite(values))
    stamp = data[[asset + '_AssetEODCompletionTime' for asset in ASSETS]].max(axis=1, skipna=False)
    pulse = mining_spread_recovers(hash_revenue(data, 'btc'), hash_revenue(data, 'bch'), stamp)
    _BTC = pulse.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_sha256_mining_recovery(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l199_btc_bch_hash_revenue_recovery_1d': dict(
        name='[RESEARCH] L199 BTC mining revenue per hash recovers vs BCH', style='momentum',
        fn=sig_sha256_mining_recovery, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
