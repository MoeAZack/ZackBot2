"""Independent formula and causal timing checks for preregistered CX2-CX4."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
import longscout_cx2 as A
import longscout_cx3 as B
import longscout_cx4 as C


def bars(seed=0,days=230):
    rng=np.random.default_rng(seed);r=rng.normal(.0002,.02,days*6)
    c=np.exp(np.cumsum(r));o=np.r_[1.,c[:-1]]
    return pd.DataFrame(dict(t=pd.date_range('2020-01-01',periods=len(c),freq='4h'),o=o,c=c,h=np.maximum(o,c)*1.01,l=np.minimum(o,c)*.99,v=np.ones(len(c))))


def ref_cusum(D,p):
    out=np.zeros((len(D),2),bool);r=np.log(D.c.to_numpy()[1:]/D.c.to_numpy()[:-1]);pos=neg=0.
    for j in range(len(D)):
        if j<p['n']+1 or D.index[j]-D.index[j-p['n']-1]!=pd.Timedelta(days=p['n']+1):
            pos=neg=0.;continue
        prior=r[j-p['n']-1:j-1]; sd=np.sqrt(sum((x-prior.mean())**2 for x in prior)/(len(prior)-1))
        if not np.isfinite(sd) or sd<=0:pos=neg=0.;continue
        z=r[j-1]/sd;pos=max(0.,pos+z-.5);neg=min(0.,neg+z+.5)
        if pos>=5:out[j,0]=True;pos=0.
        if neg<=-5:out[j,1]=True;neg=0.
    return out


def ref_coupling(D,p):
    c=D.c.to_numpy();r=np.r_[np.nan,np.log(c[1:]/c[:-1])];rho=np.full(len(c),np.nan)
    for j in range(61,len(c)):
        x=r[j-60:j];y=np.abs(r[j-59:j+1]);xm=x.mean();ym=y.mean()
        denom=np.sqrt(sum((x-xm)**2)*sum((y-ym)**2))
        if denom>0:rho[j]=sum((x-xm)*(y-ym))/denom
    return rho


class Checks(unittest.TestCase):
    def test_cusum_reference(self):
        for seed in range(3):
            D=A.L27._days_v(bars(seed))
            np.testing.assert_array_equal(A.daily_events(D,A.IP),ref_cusum(D,A.IP))

    def test_cusum_gap_reference(self):
        D=A.L27._days_v(bars()).drop(pd.Timestamp('2020-04-02'))
        np.testing.assert_array_equal(A.daily_events(D,A.IP),ref_cusum(D,A.IP))

    def test_energy_reference(self):
        for seed in range(3):
            d=bars(seed);D=B.days(d);qs=[];rs=[]
            for _,g in d.groupby(d.t.dt.date):
                w=[np.log(row.c/row.o) for row in g.itertuples()];energy=sum(v*v for v in w)
                qs.append(max(v*v for v in w)/energy if energy else 0);rs.append(sum(w))
            np.testing.assert_allclose(D.q,qs,rtol=1e-12);np.testing.assert_allclose(D.r,rs,atol=1e-14)
            for up in (False,True):
                want=[False]+[qs[j]>=.6 and qs[j-1]<.6 and (rs[j]>0 if up else rs[j]<0) for j in range(1,len(qs))]
                np.testing.assert_array_equal(B.daily_hit(D,B.IP,up),want)

    def test_energy_exact_boundary(self):
        D=B.days(bars());D.q=.59;D.r=1.;D.loc[D.index[80:83],'q']=.6
        self.assertEqual(np.flatnonzero(B.daily_hit(D,B.IP,True)).tolist(),[80])
        self.assertFalse(B.daily_hit(D,B.IP,False).any())

    def test_coupling_reference(self):
        for seed in range(3):
            D=C.L27._days_v(bars(seed));rho=ref_coupling(D,C.IP)
            np.testing.assert_allclose(C.coupling(D,C.IP),rho,atol=1e-12,equal_nan=True)
            for up in (False,True):
                want=np.zeros(len(D),bool)
                for j in range(62,len(D)):
                    if up:want[j]=rho[j]>.3 and rho[j-1]<=.3 and D.c.iloc[j]>D.c.iloc[j-20]
                    else:want[j]=rho[j]<-.3 and rho[j-1]>=-.3 and D.c.iloc[j]<D.c.iloc[j-20]
                np.testing.assert_array_equal(C.daily_hit(D,C.IP,up),want)

    def test_coupling_gap_reference(self):
        D=C.L27._days_v(bars()).drop(pd.Timestamp('2020-04-02'));rho=ref_coupling(D,C.IP)
        for up in (False,True):
            want=np.zeros(len(D),bool)
            for j in range(62,len(D)):
                if D.index[j]-D.index[j-62]!=pd.Timedelta(days=62):continue
                if up:want[j]=rho[j]>.3 and rho[j-1]<=.3 and D.c.iloc[j]>D.c.iloc[j-20]
                else:want[j]=rho[j]<-.3 and rho[j-1]>=-.3 and D.c.iloc[j]<D.c.iloc[j-20]
            np.testing.assert_array_equal(C.daily_hit(D,C.IP,up),want)

    def test_coupling_exact_boundary(self):
        D=C.L27._days_v(bars());D.c=np.arange(1.,len(D)+1)
        rho=pd.Series(.3,index=D.index);rho.iloc[80:]=.300001
        with patch.object(C,'coupling',return_value=rho):
            self.assertEqual(np.flatnonzero(C.daily_hit(D,C.IP,True)).tolist(),[80])

    def test_zero_variance(self):
        d=bars();d[['o','h','l','c']]=1.;D=A.L27._days_v(d)
        self.assertFalse(A.daily_events(D,A.IP).to_numpy().any())
        self.assertTrue((B.days(d).q==0).all())
        self.assertTrue(C.coupling(D,C.IP).isna().all())

    def test_full_signal_prefixes(self):
        d=bars();d=d.drop(index=96*6+2).reset_index(drop=True)
        for M in (A,B,C):
            for up in (False,True):
                full=M.signal(d,M.IP,up)
                for n in (721,729,923,1101,1308,1378):
                    got=M.signal(d.iloc[:n],M.IP,up)
                    for key in ('le','se','lx','sx'):
                        np.testing.assert_array_equal(np.asarray(got[key]),np.asarray(full[key])[:n])

    def test_next_day_regime_gates(self):
        d=bars();D=A.L27._days_v(d);event=pd.Series(False,index=D.index);event.iloc[130]=True
        for M in (A,B,C):
            for up in (False,True):
                for reg in (-1,0,1):
                    if M is A:
                        output=pd.DataFrame(dict(long=event,short=event));name='daily_events'
                    else:output=event;name='daily_hit'
                    with patch.object(M,name,return_value=output),patch.object(M.L1,'coin_regime',return_value=np.full(len(d),reg)):
                        got=M.signal(d,M.IP,up)
                    self.assertEqual(np.flatnonzero(got['le' if up else 'se']).tolist(),[786] if reg==(1 if up else -1) else [])

    def test_incomplete_day_excluded(self):
        d=bars().drop(index=130*6+2).reset_index(drop=True)
        self.assertNotIn(pd.Timestamp('2020-05-10'),B.days(d).index)
        self.assertNotIn(pd.Timestamp('2020-05-10'),A.L27._days_v(d).index)

    def test_registered_cases(self):
        for number,M in ((2,A),(3,B),(4,C)):
            cases=json.loads((Path(__file__).parent/f'extra31_screen/longscout_cx{number}_cases.json').read_text())
            for key,c in cases['cases'].items():
                s=M.CANDIDATES[key]
                for x,y in [('params','params'),('management','mgmt'),('tf','tf'),('side','sides')]:self.assertEqual(c[x],s[y])


if __name__=='__main__':unittest.main()
