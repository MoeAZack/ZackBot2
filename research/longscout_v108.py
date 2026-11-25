"""Research-only 1d long rotation with persistent cross-sectional rank.

At each completed UTC day, rank the positive and negative 30-day log returns
of peers with 31 contiguous closes and a prior-30-day median quote volume of
at least $10m. Require 30 eligible coins. Buy a positive-return coin entering
the top decile from a previously observed lower rank, with BTC above its
200-day SMA. Exit below the top 30%, if the rank becomes unavailable, or when
the BTC filter fails. The engine fills signals at the next daily open.
"""
import numpy as np
import pandas as pd

import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('This rotates on entry to and exit from observable cross-sectional rank bands. '
                  'V24 ranks against the coin’s own history, V40 compares only with BTC, '
                  'V94 ranks attention for entry, and V99 uses lagged peer leadership to predict catch-up.')
RUNNER = 'longscout_flow_run.py'

_RANK = _RET = _BTC_ABOVE_SMA = None


def set_panel(panel):
    global _RANK, _RET, _BTC_ABOVE_SMA
    _RANK = _RET = _BTC_ABOVE_SMA = None
    btc, _ = L101._native_daily(panel['BTCUSDT'])
    bars = {symbol: L101._native_daily(frame) for symbol, frame in panel.items() if symbol != 'BTCUSDT'}
    start = min(close.index.min() for close, _ in bars.values() if len(close))
    end = max(close.index.max() for close, _ in bars.values() if len(close))
    dates = pd.date_range(start, end, freq='D')
    closes = pd.DataFrame({s: close.reindex(dates) for s, (close, _) in bars.items()})
    volumes = pd.DataFrame({s: volume.reindex(dates) for s, (_, volume) in bars.items()})
    contiguous = closes.notna().rolling(31, min_periods=31).sum().eq(31)
    liquid = volumes.shift().rolling(30, min_periods=30).median().ge(1e7)
    _RET = np.log(closes / closes.shift(30)).where(contiguous & liquid)
    rank = _RET.rank(axis=1, method='average', pct=True)
    _RANK = rank.where(_RET.notna().sum(axis=1).ge(30), axis=0)
    btc = btc.reindex(pd.date_range(btc.index.min(), max(btc.index.max(), end), freq='D'))
    _BTC_ABOVE_SMA = btc.gt(btc.rolling(200, min_periods=200).mean()).reindex(dates, fill_value=False)


def sig_rotation(d, params, ctx):
    symbol = L101._context_symbol(ctx)
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    rank = _RANK[symbol].reindex(dates)
    ret = _RET[symbol].reindex(dates)
    btc_ok = _BTC_ABOVE_SMA.reindex(dates, fill_value=False)
    previous_rank = _RANK[symbol].shift().reindex(dates)
    entry = rank.gt(.90) & previous_rank.le(.90) & ret.gt(0) & btc_ok
    exit_ = rank.lt(.70) | rank.isna() | ~btc_ok
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(entry.to_numpy(), index=d.index)), se=flat,
                lx=S.X(pd.Series(exit_.to_numpy(), index=d.index)), sx=flat.copy())


CANDIDATES = {
    'l108_peer_rank_rotation_1d': dict(
        name='[RESEARCH] L108 peer rank rotation', style='momentum', fn=sig_rotation,
        sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=10),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
