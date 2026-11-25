"""LONGSCOUT V105 prospective OHLCV-only hypotheses, research-only/default OFF.

L105 within-day downside discontinuity: for a complete UTC day, use its six
4h close-to-close log returns (including the prior day's last close). RV=sum
r_i^2; BV=(pi/2)*(6/5)*sum_{i=2..6}|r_i||r_{i-1}|. The finite-sample
bipower-adjusted discontinuity share is max(RV-BV,0)/RV. If share>=.50 and
sum of six returns<=-.04, first full condition, R1 rising/sideways -> long.
This is a noisy 4h-bar decomposition, not proof of an individual market jump.
Previous and current UTC days must both be complete; previous event requires
one further complete day. The calculation differs from V84's daily signed
semivariance and V91's prior-day Rogers-Satchell volatility normalization.

R105 volume-conditioned reversal: for day j, daily log return r and quote
volume q; high-volume day t has q_t>=1.2*median(q[t-20..t-1]). Over the 60
predictor days t=j-61..j-2, collect each high-volume up/down day's NEXT daily
return r[t+1], all known before j. Need >=8 observations per direction; mean
next return after up days <=-.002 and after down days >=.002. Current day j
must itself be high-volume: r_j<0 -> long, r_j>0 -> short; first full condition
per side, coin R1 sideways. 83 consecutive complete valid days required,
including the previous day's model for the edge. This learns sign-conditional
next-day outcomes rather than V95's lagged volume/return linear correlation
or V21's unconditional return autocorrelation; no profitability is assumed.

Both daily-close decisions map to next-day 00-open4h signal, modeled 04 fill;
2.5 ATR/2R/30-bar cap. No external input, parameter search, or market outcome.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v92 as H


def _days(d):
    """Aggregate only exact chronological UTC 4h bins; retain invalid qv as NaN."""
    t = pd.to_datetime(d.t)
    if t.isna().any() or t.dt.tz is not None or not t.is_monotonic_increasing or t.duplicated().any():
        raise ValueError('4h timestamps must be sorted unique UTC-naive values')
    D = H.daily(d)
    groups = t.groupby(t.dt.normalize())
    complete = groups.apply(lambda x: list(x) == [x.iloc[0].normalize() + pd.Timedelta(hours=h) for h in range(0,24,4)])
    D = D[complete.reindex(D.index, fill_value=False)]
    q = pd.to_numeric(d.qv, errors='coerce').where(lambda x: np.isfinite(x) & (x >= 0))
    D['qv'] = q.groupby(t.dt.normalize()).sum(min_count=6).reindex(D.index)
    c = pd.to_numeric(D.c, errors='coerce')
    D['c'] = c.where(np.isfinite(c) & (c > 0))
    return D


def discontinuity_statistics(d):
    """Daily RV, BV and share from exactly six consecutive 4h returns."""
    D = _days(d)
    t = pd.to_datetime(d.t)
    c = pd.to_numeric(d.c, errors='coerce')
    c = c.where(np.isfinite(c) & (c > 0))
    ret = np.log(c / c.shift())
    continuous = t.diff().eq(pd.Timedelta(hours=4)).rolling(6, min_periods=6).sum().eq(6)
    rv = (ret*ret).rolling(6, min_periods=6).sum()
    bv = (np.pi/2)*(6/5)*(ret.abs()*ret.abs().shift()).rolling(5,min_periods=5).sum()
    share = ((rv-bv).clip(lower=0)/rv.where(rv>0)).where(continuous)
    rows = t.dt.hour.eq(20)
    index = pd.DatetimeIndex(t[rows].dt.normalize())
    stats = pd.DataFrame({'rv':rv[rows].to_numpy(), 'bv':bv[rows].to_numpy(),
                          'share':share[rows].to_numpy()}, index=index).reindex(D.index)
    previous_complete = L27._consec(D,1)
    return D, stats.where(previous_complete, axis=0)


def discontinuity_flags(d):
    D, st = discontinuity_statistics(d)
    r = np.log(D.c / D.c.shift())
    hit = ((st['share'] >= .50) & (r <= -.04) & L27._consec(D,1)).fillna(False)
    consecutive = D.index.to_series().diff().eq(pd.Timedelta(days=1))
    return D, (hit & ~(hit.shift(fill_value=False) & consecutive)).fillna(False)


def conditional_statistics(D):
    """Prior-60 predictor days and their next-day returns, known before j."""
    r = np.log(D.c / D.c.shift())
    q = D.qv
    median = q.shift().rolling(20,min_periods=20).median()
    high = (q >= 1.2*median) & (median > 0)
    up = high & (r > 0)
    down = high & (r < 0)
    # At row j these series end at outcome j-1 (predictor j-2).
    n_up = up.shift(2,fill_value=False).astype(float).rolling(60,min_periods=60).sum()
    n_down = down.shift(2,fill_value=False).astype(float).rolling(60,min_periods=60).sum()
    sum_up = (up.shift(1,fill_value=False)*r).shift().rolling(60,min_periods=60).sum()
    sum_down = (down.shift(1,fill_value=False)*r).shift().rolling(60,min_periods=60).sum()
    return pd.DataFrame({'r':r, 'high':high, 'n_up':n_up, 'n_down':n_down,
                         'mean_up':sum_up/n_up.replace(0,np.nan),
                         'mean_down':sum_down/n_down.replace(0,np.nan)}, index=D.index)


def conditional_flags(d):
    D = _days(d)
    st = conditional_statistics(D)
    valid = D.c.notna() & D.qv.notna()
    ready = valid.rolling(83,min_periods=83).sum().eq(83) & L27._consec(D,82)
    model = ((st.n_up >= 8) & (st.n_down >= 8) & (st.mean_up <= -.002)
             & (st.mean_down >= .002) & st.high & ready)
    lo = (model & (st.r < 0)).fillna(False)
    hi = (model & (st.r > 0)).fillna(False)
    return D, dict(long=(lo & ~lo.shift(fill_value=False)).fillna(False),
                   short=(hi & ~hi.shift(fill_value=False)).fillna(False))


def _empty(d):
    return {k: pd.Series(False,index=d.index) for k in ('le','se','lx','sx')}


def sig_discontinuity(d,p,ctx):
    D,flag = discontinuity_flags(d)
    out = _empty(d)
    gate = np.isin(L1.coin_regime(d),(0.,1.))
    out['le'] = S.X(pd.Series(L27._next_day(flag,D,d) & gate,index=d.index))
    return out


def sig_conditional(d,p,ctx):
    D,flags = conditional_flags(d)
    out = _empty(d)
    gate = L1.coin_regime(d)==0
    for side,key in (('le','long'),('se','short')):
        out[side] = S.X(pd.Series(L27._next_day(flags[key],D,d) & gate,index=d.index))
    return out


FAMILY_NOVELTY = ('The long tests downside RV minus bipower variation, distinct from V84 signed '
                  'semivariance and V91 range-volatility normalization. The range arm learns '
                  'next-day reversal by high-volume return sign, distinct from V21 autocorrelation '
                  'and V95 lagged volume/return correlation.')

CANDIDATES = {
    'l105_downside_bipower_discontinuity_4h': dict(name='[RESEARCH] L105 downside bipower discontinuity',style='reversal',
        fn=sig_discontinuity,sides='long',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
    'r105_volume_conditional_reversal_4h': dict(name='[RESEARCH] R105 volume conditional reversal',style='range',
        fn=sig_conditional,sides='both',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
}


def register():
    for key,value in CANDIDATES.items(): S.STRATEGIES[key]=dict(value,desc=value['name'])
