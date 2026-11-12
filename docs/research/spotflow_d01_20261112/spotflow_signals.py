"""SPOTFLOW-D01 closed-bar signal functions; research only."""
import numpy as np
import pandas as pd

def prior_complete_days_elig(qv):
    """Prior 30 complete UTC days, exactly 720 consecutive finite nonnegative hours."""
    if not isinstance(qv.index, pd.DatetimeIndex) or not qv.index.is_unique: raise ValueError('unique datetime index required')
    grid=qv.sort_index().asfreq('h')
    valid=grid.where(np.isfinite(grid)&(grid>=0))
    daily=valid.groupby(valid.index.floor('D')).sum(min_count=24).asfreq('D')
    med=daily.rolling(30,min_periods=30).median().shift(1)
    snapshot=med.reindex(grid.index.floor('D')).to_numpy()
    run=valid.notna().rolling(720,min_periods=720).sum()
    return pd.Series((snapshot>=10_000_000)&(run.to_numpy()==720),index=grid.index)

def wilder_atr(frame):
    """RSR indicator: first true range seed, ewm(alpha=1/14, adjust=False)."""
    f=frame.sort_index().asfreq('h'); c=f.c; prev=c.shift()
    tr=pd.concat([f.h-f.l,(f.h-prev).abs(),(f.l-prev).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/14,adjust=False).mean()

def signal(spot,perp,price,btc,atr=None):
    """Aligned hourly indexes; price return uses three consecutive returns ending t."""
    idx=price.index; s=spot.reindex(idx); p=perp.reindex(idx); b=btc.reindex(idx)
    ok=(s.qv>0)&(p.qv>0)&s.tbqv.between(0,s.qv)&p.tbqv.between(0,p.qv)
    ok &= prior_complete_days_elig(spot.qv).reindex(idx,fill_value=False)
    r=np.log(price.c/price.c.shift(3)); br=np.log(b.c/b.c.shift(3))
    # asfreq grid means all three intermediate hours and predecessor are needed.
    ok &= price.c.rolling(4,min_periods=4).count().eq(4)&b.c.rolling(4,min_periods=4).count().eq(4)
    atr=wilder_atr(price) if atr is None else atr.reindex(idx)
    ok &= np.isfinite(atr)&(atr>0)&(br.abs()<.015)&(price.c>0)
    long=ok&(5*s.tbqv>=3*s.qv)&(5*p.tbqv<=2*p.qv)&(r<=-atr/price.c)
    short=ok&(5*s.tbqv<=2*s.qv)&(5*p.tbqv>=3*p.qv)&(r>=atr/price.c)
    return long.astype(int)-short.astype(int), atr
