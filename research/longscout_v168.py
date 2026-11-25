"""Research-only range fade after alt account positioning diverges.

For five fixed liquid Binance futures, subtract their own trailing 60-day
median log long/short account ratio from each completed day's log ratio.
Require dispersion above 0.25 log points and twice its prior 40-day median,
with the current cross-market median within 0.05 log points of normal.
Fade a sideways target's 2% move on the first shock while BTC is nonbear.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import longscout_v161 as L161
import strategies as S

FAMILY_NOVELTY = ('Tests dispersion of relative account positioning while the typical market '
                  'remains near normal. V161 tests broad directional agreement, V142 '
                  'compares cohorts within BTC, and V94 measures return dispersion.')
ALT_ACCOUNT_BREADTH_SHA256 = L161.ALT_ACCOUNT_BREADTH_SHA256
MARKET_FEATURE = ('ALT_ACCOUNT_BREADTH_SHA256', 'account ratio dispersion across five alt futures')
_BTC = None


def disagreement_shock(ratios):
    log = np.log(ratios.where(np.isfinite(ratios) & ratios.gt(0)))
    normal = log.shift().rolling(60, min_periods=50).median()
    relative = log - normal
    complete = relative.notna().all(axis=1)
    width = (relative.max(axis=1) - relative.min(axis=1)).where(complete)
    center = relative.median(axis=1).where(complete)
    reference = width.shift().rolling(40, min_periods=35).median()
    observed = reference.notna() & width.notna()
    state = observed & width.ge(.25) & width.ge(2. * reference) & center.abs().le(.05)
    return state & observed.shift(fill_value=False) & ~state.shift(fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    event = disagreement_shock(L161.load_ratios())
    _BTC = event.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_positioning_disagreement(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).eq(0)
    move = grid.pct_change(fill_method=None)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series((active & move.le(-.02)).reindex(close.index).to_numpy(), index=d.index)),
                se=S.X(pd.Series((active & move.ge(.02)).reindex(close.index).to_numpy(), index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r168_alt_account_position_disagreement_1d': dict(
        name='[RESEARCH] R168 Alt account-positioning dispersion fade', style='range',
        fn=sig_positioning_disagreement, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
