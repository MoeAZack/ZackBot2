"""Research-only long after the Treasury balance drops and bank reserves rise.

Use release-dated Federal Reserve H.4.1 Wednesday balances. Compared with
the previous week, require a Treasury General Account drawdown of at least
$35 billion and a reserve balance increase of at least $30 billion, with
Reserve Bank credit changing by no more than $20 billion. Use the first
qualifying week after the archive release plus one full UTC day. Require
BTC and target nonbear and the target close rising.
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

FAMILY_NOVELTY = ('Tests a Treasury balance drawdown coinciding with an increase in bank reserves '
                  'without material change in Fed credit. V140 looks at on-chain token supply, '
                  'V173 at sovereign real yields and V175 at overnight repo costs.')
FED_RESERVE_BALANCES_SHA256 = 'df660ea05d747056e0393a049c837b2019af73cd06ee28e9ed28bcd40f75d796'
MARKET_FEATURE = ('FED_RESERVE_BALANCES_SHA256', 'archived release-dated Treasury liabilities and bank reserves')
MARKET_FEATURE_SOURCE = 'Federal Reserve H.4.1 Table 1 Wednesday level series'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_fed_reserves_20261125/manifest.json'
FIELDS = ['reserve_balances', 'treasury_general_account', 'reserve_bank_credit']
_BTC = None


def load_releases():
    raw = Path(os.environ.get('LONGSCOUT_FED_RESERVE_BALANCES', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != FED_RESERVE_BALANCES_SHA256:
        raise ValueError('expected pinned H.4.1 releases')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields'], data['units']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1w', FIELDS, 'millions of U.S. dollars',
    ):
        raise ValueError('invalid H.4.1 balance source contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    published = pd.DatetimeIndex(pd.to_datetime([row[1] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='W-WED')):
        raise ValueError('Fed observation weeks must be unique and consecutive')
    valid = published.notna()
    if (not published[valid].is_unique or (published[valid] < dates[valid]).any()
            or (published[valid] > dates[valid] + pd.Timedelta(days=5)).any()):
        raise ValueError('Fed archive releases must follow valid observation dates')
    return pd.DataFrame([row[2:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64'), published


def reserves_release(balances, published):
    released = pd.Series(published.notna(), index=balances.index)
    ready = (np.isfinite(balances) & balances.gt(0)).all(axis=1) & released
    change = balances.where(ready, axis=0).diff()
    eligible = ready & ready.shift(fill_value=False)
    event = (eligible & change.reserve_balances.ge(30_000)
             & change.treasury_general_account.le(-35_000) & change.reserve_bank_credit.abs().le(20_000))
    first = event & eligible.shift(fill_value=False) & ~event.shift(fill_value=False)
    # A label one day after the release closes two UTC midnights after it.
    decision_labels = published[released.to_numpy()] + pd.Timedelta(days=1)
    return first.loc[released].set_axis(decision_labels)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    receipts, published = load_releases()
    pulse = reserves_release(receipts, published).reindex(btc.index, fill_value=False)
    _BTC = pulse & L1.regime_daily(btc).isin((0., 1.))


def sig_reserves_release(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l182_treasury_drawdown_reserve_increase_1d': dict(
        name='[RESEARCH] L182 Bank reserve increase during Treasury drawdown', style='momentum',
        fn=sig_reserves_release, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
