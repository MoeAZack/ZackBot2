"""LONGSCOUT V106 prospective native-daily OHLCV hypotheses, default OFF.

L106 instrument-specific weekday persistence: at completed UTC day j, for
NEXT day's weekday, examine that weekday's previous eight close-to-close daily
log returns, on days j-6, j-13, ..., j-55. At least seven must be positive and
their arithmetic mean >=.003. Enter on the first full statistical condition,
then require own R1 rising/sideways. Require 58 complete consecutive valid daily
closes so both the current and previous daily conditions are computable.
This learns each instrument's next-weekday pattern from prior observations;
it differs from an unconditional Monday or weekend hold. The signal's modeled
entry is at the next daily open; the prediction is for that upcoming day.

R106 daily transaction-VWAP displacement: the complete day's transaction VWAP
is quote volume / base volume, using both aggregated native daily kline fields.
Close <=.97*VWAP -> long, >=1.03*VWAP -> short, first full condition per side;
coin R1 sideways. At least two consecutive valid daily bars are required.
This tests same-day settlement against its own traded-volume benchmark, not
V11's rolling 30-day VWAP trend cross, V5's swing-anchored VWAP reclaim, or
V102's approximated volume-at-price POC. VWAP is a historical trade-weighted
benchmark, not an execution price or evidence of actual order flow.

Both are 1d-close signals with next-day-open modeled fill; 2 daily ATR stop,
2R target, 5-day cap. Fixed prospectively; no grid or profitability claim.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27


def native_days(d):
    """Validate native day labels and expose invalid values as unavailable."""
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    if dates.hasnans or dates.tz is not None or not dates.is_unique or not dates.is_monotonic_increasing or (dates != dates.normalize()).any():
        raise ValueError('daily timestamps must be sorted unique UTC midnights')
    result = pd.DataFrame(index=dates)
    for key in ('c','v','qv'):
        values = pd.to_numeric(pd.Series(d[key].to_numpy(),index=dates),errors='coerce')
        result[key] = values.where(np.isfinite(values) & (values > 0))
    return result


def weekday_statistics(D):
    """Use returns of eight previous occurrences of tomorrow's UTC weekday."""
    r = np.log(D.c / D.c.shift())
    # The oldest used return is j-55; the next day is j+1.
    values = pd.concat([r.shift(k) for k in (6,13,20,27,34,41,48,55)],axis=1)
    count = values.notna().sum(axis=1)
    positive = values.gt(0).sum(axis=1)
    mean = values.mean(axis=1).where(count==8)
    return pd.DataFrame({'count':count,'positive':positive,'mean':mean},index=D.index)


def weekday_flags(D):
    st = weekday_statistics(D)
    ready = D.c.notna().rolling(58,min_periods=58).sum().eq(58) & L27._consec(D,57)
    hit = ((st['count']==8) & (st['positive']>=7) & (st['mean']>=.003) & ready).fillna(False)
    return (hit & ~hit.shift(fill_value=False)).fillna(False)


def vwap_statistics(D):
    """Return the day's exact aggregated transaction-value / base-volume VWAP."""
    vwap = D.qv / D.v
    return pd.DataFrame({'vwap':vwap.where(np.isfinite(vwap) & (vwap > 0))},index=D.index)


def vwap_flags(D):
    vwap = vwap_statistics(D).vwap
    valid = D.c.notna() & vwap.notna()
    ready = valid.rolling(2,min_periods=2).sum().eq(2) & L27._consec(D,1)
    low = ((D.c <= .97*vwap) & ready).fillna(False)
    high = ((D.c >= 1.03*vwap) & ready).fillna(False)
    return dict(long=(low & ~low.shift(fill_value=False)).fillna(False),
                short=(high & ~high.shift(fill_value=False)).fillna(False))


def _empty(d):
    return {key:pd.Series(False,index=d.index) for key in ('le','se','lx','sx')}


def sig_weekday(d,p,ctx):
    D = native_days(d)
    out = _empty(d)
    regime = L1.regime_daily(D.c).to_numpy()
    out['le'] = S.X(pd.Series(weekday_flags(D).to_numpy() & np.isin(regime,(0.,1.)),index=d.index))
    return out


def sig_vwap(d,p,ctx):
    D = native_days(d)
    flags = vwap_flags(D)
    out = _empty(d)
    regime = L1.regime_daily(D.c).to_numpy()
    for side,key in (('le','long'),('se','short')):
        out[side] = S.X(pd.Series(flags[key].to_numpy() & (regime==0),index=d.index))
    return out


FAMILY_NOVELTY = ('The long learns which weekdays historically worked for each coin, distinct from '
                  'the fixed Monday and weekend holds in V18/V31. The range arm fades settlement '
                  'against the same-day transacted price, distinct from rolling VWAP trend crosses '
                  'in V11/V32 and swing-anchored trend reclaims in V5/V65.')

CANDIDATES = {
    'l106_learned_weekday_1d':dict(name='[RESEARCH] L106 prior weekday persistence',style='momentum',
        fn=sig_weekday,sides='long',tf='1d',research_only=True,params={},mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=5)),
    'r106_daily_vwap_displacement_1d':dict(name='[RESEARCH] R106 daily transaction-VWAP displacement',style='range',
        fn=sig_vwap,sides='both',tf='1d',research_only=True,params={},mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=5)),
}


def register():
    for key,value in CANDIDATES.items(): S.STRATEGIES[key]=dict(value,desc=value['name'])
