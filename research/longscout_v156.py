"""Research-only range fade after BTC's paid funding diverges from its premium.

Match each 4h premium close with the last strictly observed, cadence-valid
funding settlement normalized to eight hours. Positive paid funding of at
least two basis points with a current discount of two basis points signals
possible relief for a sideways coin that fell 1.5% or more. Use the reverse
case to fade a rise. Require nonbear BTC and the first observed divergence.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v117 as L117
import longscout_v120 as L120
import longscout_v143 as L143
import strategies as S

FAMILY_NOVELTY = ('Pairs a past settled payment with the opposite sign of the current premium '
                  'on the same BTC contract. V120 uses premium convergence alone, V143 '
                  'compares funding between coins, and V135 compares two funding payments.')
RUNNER = 'longscout_flow_run.py'
DATA_MANIFEST_SHA256 = L117.DATA_MANIFEST_SHA256
BTC_PREMIUM_MANIFEST_SHA256 = L120.BTC_PREMIUM_MANIFEST_SHA256
set_panel = L117.set_panel
_MARKET = None


def divergence(paid, premium):
    observed = np.isfinite(paid) & np.isfinite(premium)
    long = observed & paid.ge(.0002) & premium.le(-.0002)
    short = observed & paid.le(-.0002) & premium.ge(.0002)
    before = observed.shift(fill_value=False)
    return pd.DataFrame({'long': long & before & ~long.shift(fill_value=False),
                         'short': short & before & ~short.shift(fill_value=False)})


def set_btc(frame):
    global _MARKET
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    grid = pd.date_range(dates[0], dates[-1], freq='4h')
    close = (grid + pd.Timedelta(hours=4)).as_unit('ms').asi8
    paid = pd.Series(L143.observed_funding(close, L117.load_funding('BTCUSDT')), index=grid)
    premium = L120.load_premium().reindex(grid)
    nonbear = pd.Series(np.isin(L1.coin_regime(frame), (0., 1.)), index=dates)
    _MARKET = divergence(paid, premium).where(nonbear.reindex(grid, fill_value=False), False, axis=0)


def sig_funding_premium_divergence(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    grid = pd.date_range(dates[0], dates[-1], freq='4h')
    close = pd.Series(d.c.to_numpy(), index=dates).reindex(grid)
    move = close.pct_change(fill_method=None).reindex(dates).to_numpy()
    sideways = L1.coin_regime(d) == 0
    market = _MARKET.reindex(dates, fill_value=False)
    long = market['long'].to_numpy() & sideways & (move <= -.015)
    short = market['short'].to_numpy() & sideways & (move >= .015)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(long, index=d.index)), se=S.X(pd.Series(short, index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r156_btc_settled_funding_premium_divergence_4h': dict(
        name='[RESEARCH] R156 BTC paid funding versus premium range fade', style='range',
        fn=sig_funding_premium_divergence, sides='both', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
