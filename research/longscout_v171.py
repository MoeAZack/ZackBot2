"""Research-only long on asymmetric BTC price response to aggressive flow.

Fit separate through-origin coefficients of BTC close-to-close log returns
on signed quote imbalance over the preceding 60 completed 4h bars. Require
a fully paired window and 12 observations per sign. Buy after the buy-side
coefficient first exceeds the nonnegative sell-side coefficient by 0.003
per unit of imbalance. BTC and target must close up in nonbear R1.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v136 as L136
import strategies as S

FAMILY_NOVELTY = ('Compares estimated price sensitivity to buyer- and seller-initiated flow. '
                  'V82 uses unsigned realized impact, V136 uses a chronological reversal in '
                  'flow, and V98 conditions on one bar of passive absorption.')
BTC_SIGNED_FLOW_MANIFEST_SHA256 = L136.BTC_SIGNED_FLOW_MANIFEST_SHA256
MARKET_FEATURE = ('BTC_SIGNED_FLOW_MANIFEST_SHA256', 'BTC taker-buy and total quote turnover')
_BTC = None


def response_asymmetry(returns, imbalance):
    valid = np.isfinite(returns) & np.isfinite(imbalance) & imbalance.between(-1, 1)
    x, y = imbalance.where(valid), returns.where(valid)
    product, square = x * y, x.pow(2)

    def coefficient(sign):
        count = sign.shift().rolling(60, min_periods=60).sum()
        numerator = product.where(sign, 0.).shift().rolling(60, min_periods=60).sum()
        denominator = square.where(sign, 0.).shift().rolling(60, min_periods=60).sum()
        return numerator / denominator.where(count.ge(12) & denominator.gt(0))

    paired = valid.shift().rolling(60, min_periods=60).sum().eq(60)
    buying, selling = coefficient(x.gt(0)), coefficient(x.lt(0))
    spread = (buying - selling).where(paired & selling.ge(0))
    return spread.ge(.003) & spread.shift().lt(.003)


def set_btc(frame):
    global _BTC
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    grid = pd.date_range(dates[0], dates[-1], freq='4h')
    close = pd.Series(frame.c.to_numpy(), index=dates).reindex(grid)
    returns = np.log(close.where(np.isfinite(close) & close.gt(0))).diff()
    flow = L136.load_flow().reindex(grid)
    total, taker = flow.quote_turnover, flow.taker_buy_quote_turnover
    valid = np.isfinite(total) & np.isfinite(taker) & total.gt(0) & taker.ge(0) & taker.le(total)
    imbalance = (2 * taker / total - 1).where(valid)
    nonbear = pd.Series(np.isin(L1.coin_regime(frame), (0., 1.)), index=dates)
    _BTC = response_asymmetry(returns, imbalance) & returns.gt(0) & nonbear.reindex(grid, fill_value=False)


def sig_buy_impact(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    consecutive = dates.to_series().diff().eq(pd.Timedelta(hours=4)).to_numpy()
    enter = (_BTC.reindex(dates, fill_value=False).to_numpy() & consecutive
             & d.c.diff().gt(0).to_numpy() & np.isin(L1.coin_regime(d), (0., 1.)))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter, index=d.index)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l171_btc_flow_response_asymmetry_4h': dict(
        name='[RESEARCH] L171 BTC flow-response asymmetry long', style='momentum',
        fn=sig_buy_impact, sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
