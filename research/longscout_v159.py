"""Research-only long after predicted BTC liquidation pressure subsides.

Fit a ridge model to observed daily sell-liquidation snapshots from the
BTC COIN-M perpetual. Features are completed BTC return/range, COIN-M open
interest contraction and signed COIN-M taker flow. Use 120 valid known labels
and a two-day publication delay. After the observed archive ends, retain the
last causal fit and report the later prediction window as unlabeled.

Mark high pressure at the past label mean plus one population standard
deviation. Enter after a negative BTC pressure day and then a positive,
lower-pressure BTC day in nonbear regimes with a rising target. The model
predicts archived snapshot activity. It does not recover missing force orders.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import longscout_v155 as L155
import longscout_v158 as L158
import strategies as S

FAMILY_NOVELTY = ('Calibrates a market-pressure model to causal labels from observed forced-order '
                  'snapshots. V118 treats OI contraction as a rule without these labels, '
                  'and V135/V155/V158 use margin-market comparisons without a target label.')
LIQUIDATION_LABEL_MANIFEST_SHA256 = 'b0c4c9dccc0598ea21587cb8b2217b4ef3b814a733205e53f4c15dc8e2d3292c'
MARKET_FEATURE = ('LIQUIDATION_LABEL_MANIFEST_SHA256', 'observed BTC COIN-M forced sell contract counts')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_liquidation_labels_20261125/manifest.json'
_BTC = None


def load_labels():
    raw = Path(os.environ.get('LONGSCOUT_LIQUIDATION_LABELS_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != LIQUIDATION_LABEL_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC liquidation labels')
    data = json.loads(raw)
    field = 'observed_sell_filled_contracts'
    if (data['status'], data['instrument'], data['field']) != ('COMPLETE_WITH_EXPLICIT_MASKS', 'BTCUSD_PERP', field):
        raise ValueError('invalid BTC liquidation label input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row['date'] for row in data['days']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('BTC liquidation label dates must be continuous and unique')
    counts = pd.to_numeric(pd.Series([row.get(field) for row in data['days']], index=dates))
    return np.log1p(counts.where(np.isfinite(counts) & counts.ge(0)))


def forecast_pressure(features, labels):
    x = np.column_stack((np.ones(len(features)), features.to_numpy(dtype=float)))
    y = labels.to_numpy(dtype=float)
    valid = np.isfinite(x).all(axis=1)
    penalty = np.eye(x.shape[1])
    penalty[0, 0] = 0.
    covariance, products = np.zeros_like(penalty), np.zeros(x.shape[1])
    count, total, total_square = 0, 0., 0.
    margin = np.full(len(x), np.nan)
    weights = None
    for position in range(len(x)):
        lag = position - 2
        if lag >= 0 and valid[lag] and np.isfinite(y[lag]):
            covariance += np.outer(x[lag], x[lag])
            products += x[lag] * y[lag]
            count += 1
            total += y[lag]
            total_square += y[lag] ** 2
            if count >= 120:
                weights = np.linalg.solve(covariance + penalty, products)
        if weights is not None and valid[position]:
            mean = total / count
            std = np.sqrt(max(0., total_square / count - mean ** 2))
            if std > 0:
                margin[position] = x[position] @ weights - (mean + std)
    return pd.Series(margin, index=features.index)


def pressure_eases(price_return, margin):
    return margin.shift().ge(0) & margin.lt(0) & price_return.shift().lt(0) & price_return.gt(0)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    bars = pd.DataFrame({'close': btc, 'high': frame.h.to_numpy(), 'low': frame.l.to_numpy()})
    dates = pd.date_range(btc.index[0], btc.index[-1], freq='D')
    bars = bars.reindex(dates)
    close = bars.close
    change = close.pct_change(fill_method=None)
    oi = L155.load_collateral().BTCUSD_PERP.reindex(dates)
    _, inverse = L158.load_flows()
    eligible = L158.signed_imbalance(inverse.total, inverse.buy).notna()
    volume = inverse.where(eligible).resample('D').sum(min_count=6).reindex(dates)
    sell = 1 - 2 * volume.buy / volume.total
    features = pd.DataFrame({'down_move': (-change).clip(lower=0) / .02,
                             'range': ((bars.high - bars.low) / close) / .05,
                             'contract_drop': (-oi.pct_change(fill_method=None)).clip(lower=0) / .05,
                             'net_sell': sell * 4})
    margin = forecast_pressure(features, load_labels().reindex(dates))
    _BTC = pressure_eases(change, margin) & L1.regime_daily(close).isin((0., 1.))


def sig_liquidation_proxy_recovery(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l159_btc_observed_liquidation_proxy_recovery_1d': dict(
        name='[RESEARCH] L159 BTC observed-liquidation proxy recovery long', style='momentum',
        fn=sig_liquidation_proxy_recovery, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
