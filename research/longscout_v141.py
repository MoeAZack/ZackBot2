"""Research-only daily long when older BTC outputs account for less spending.

Compute the seven-day volume-weighted share of spent outputs aged at least
365 days. Compare with the weighted share over the preceding 30 days. Buy
the first cross from above the reference to at or below it, after at least
one prior weekly observation had a 1.5x share. Apply seven full UTC days of
lag to reconstruct the signal from an archived chain snapshot. Require
nonbear BTC and target regimes and a positive target return.
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

FAMILY_NOVELTY = ('Uses the age distribution of spent Bitcoin outputs rather than transaction '
                  'fees/counts, mining work or issuance on another chain. The tested input is '
                  'an older-output spending share, with no assumed sale or wallet attribution.')
DORMANCY_MANIFEST_SHA256 = 'e6ac90d1ab49cdf106ea735a9360f355780669f0fb38b863e2492d7e7c770b04'
MARKET_FEATURE = ('DORMANCY_MANIFEST_SHA256', 'daily BTC output value spent and aged-at-least-365-day component')
MARKET_FEATURE_SOURCE = 'Open Bitcoin Metrics release 0.1.0, Bitcoin Core full-node data'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_dormancy_20261125/manifest.json'
_BTC = None


def load_spent_value():
    raw = Path(os.environ.get('LONGSCOUT_BTC_DORMANCY_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != DORMANCY_MANIFEST_SHA256:
        raise ValueError('expected pinned Bitcoin output-age input')
    data = json.loads(raw)
    fields = ['spent_365d_btc', 'spent_total_btc']
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE', '1d', fields):
        raise ValueError('invalid Bitcoin output-age input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('Bitcoin output-age dates must be continuous and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields).apply(pd.to_numeric, errors='raise')


def older_spending_relief(older, total):
    valid = np.isfinite(older) & np.isfinite(total) & older.ge(0) & total.gt(0) & older.le(total)
    older, total = older.where(valid), total.where(valid)
    recent = older.rolling(7, min_periods=7).sum() / total.rolling(7, min_periods=7).sum()
    older_base = older.shift(7).rolling(30, min_periods=30).sum()
    total_base = total.shift(7).rolling(30, min_periods=30).sum()
    baseline = (older_base / total_base).where(older_base.gt(0))
    ratio = recent / baseline
    prior = ratio.shift()
    prior_peak = prior.rolling(7, min_periods=7).max()
    return (prior.gt(1.) & ratio.le(1.) & prior_peak.ge(1.5)).shift(7, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    archive = load_spent_value()
    relief = older_spending_relief(archive.spent_365d_btc, archive.spent_total_btc)
    _BTC = relief.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_older_spending_relief(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l141_older_btc_spending_relief_1d': dict(
        name='[RESEARCH] L141 Older BTC output spending relief long', style='momentum',
        fn=sig_older_spending_relief, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
