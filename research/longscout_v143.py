"""Research-only range fade after a coin's relative funding extreme eases.

For every UTC daily decision rank the latest observable perpetual funding
rates per eight hours across at least 60 contemporaneously quoted peers.
Trade a coin in sideways R1 after exiting the bottom/top decile toward the
middle, while BTC is rising or sideways. Exit at the peer median, on lost
coverage or when the range conditions fail. Absolute funding sign is unused.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import longscout_v117 as L117
import strategies as S

FAMILY_NOVELTY = ('Fades relative carry extremes among native contracts. V117 buys an absolute '
                  'negative funding squeeze with a price breakout; V123 tests cadence; V135 '
                  'tests two BTC collateral legs. V108 ranks past returns, not relative carry.')
RUNNER = 'longscout_flow_run.py'
DATA_MANIFEST_SHA256 = L117.DATA_MANIFEST_SHA256
_RANK = _BTC = None


def observed_funding(cutoff, funding):
    stamp = funding.calc_time.to_numpy(dtype=np.int64)
    interval = funding.funding_interval_hours.to_numpy(dtype=np.int64)
    rate = funding.last_funding_rate.to_numpy(dtype=float)
    if not (np.all(np.diff(stamp) > 0) and np.all((interval >= 1) & (interval <= 24))
            and np.all(np.isfinite(rate))):
        raise ValueError('invalid frozen funding rows')
    previous = np.searchsorted(stamp, cutoff, side='left') - 1
    ready = previous >= 1
    known = previous[ready]
    pre = known - 1
    age = cutoff[ready] - stamp[known]
    gap = stamp[known] - stamp[pre]
    valid = ((age <= interval[known] * L117.HOUR_MS + 60_000)
             & (gap >= L117.HOUR_MS - 60_000)
             & (gap <= np.maximum(interval[known], interval[pre]) * L117.HOUR_MS + 60_000))
    output = np.full(len(cutoff), np.nan)
    output[ready] = np.where(valid, rate[known] * 8 / interval[known], np.nan)
    return output


def funding_rank(panel):
    ranks = panel.rank(axis=1, method='average', pct=True)
    return ranks.where(panel.notna().sum(axis=1).ge(60), axis=0)


def set_panel(panel):
    global _RANK, _BTC
    L117.set_panel(panel)
    btc, _ = L101._native_daily(panel['BTCUSDT'])
    dates = pd.date_range(btc.index[0], btc.index[-1], freq='D')
    cutoff = (dates + pd.Timedelta(days=1)).as_unit('ms').asi8
    values = {}
    for symbol, frame in panel.items():
        if symbol == 'BTCUSDT':
            continue
        close, _ = L101._native_daily(frame)
        rates = pd.Series(observed_funding(cutoff, L117.load_funding(symbol)), index=dates)
        values[symbol] = rates.where(close.reindex(dates).notna())
    _RANK = funding_rank(pd.DataFrame(values, index=dates))
    _BTC = L1.regime_daily(btc.reindex(dates)).isin((0., 1.))


def range_flags(rank, previous, eligible):
    long = eligible & previous.le(.10) & rank.gt(.10) & rank.lt(.50)
    short = eligible & previous.ge(.90) & rank.lt(.90) & rank.gt(.50)
    return long, short, ~eligible | rank.isna() | rank.ge(.50), ~eligible | rank.isna() | rank.le(.50)


def sig_relative_carry(d, params, ctx):
    close, _ = L101._native_daily(d)
    symbol = L101._context_symbol(ctx)
    dates = close.index
    full = close.reindex(pd.date_range(dates[0], dates[-1], freq='D'))
    eligible = L1.regime_daily(full).eq(0) & _BTC.reindex(full.index, fill_value=False)
    rank = _RANK[symbol]
    flags = range_flags(rank.reindex(full.index), rank.shift().reindex(full.index), eligible)
    return {name: S.X(pd.Series(flag.reindex(dates).to_numpy(), index=d.index))
            for name, flag in zip(('le', 'se', 'lx', 'sx'), flags)}


CANDIDATES = {
    'r143_relative_funding_carry_fade_1d': dict(
        name='[RESEARCH] R143 Relative funding carry fade', style='range', fn=sig_relative_carry,
        sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
