"""Independent formulas, availability and causal-prefix tests for frozen CX1."""
import itertools
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
import longscout_cx1 as C


def daily(seed=1, size=400):
    rng = np.random.default_rng(seed)
    c = np.exp(np.cumsum(rng.normal(0, .04, size)))
    return pd.DataFrame(dict(c=c, h=c*1.02, l=c*.98, v=np.ones(size)),
                        index=pd.date_range('2020-01-01', periods=size, freq='D'))


def reference(D, up):
    c = D.c.to_numpy(); out = pd.Series(False, index=D.index)
    def h(j):
        counts = {}
        for end in range(j-59, j+1):
            r = [np.log(c[k]/c[k-1]) for k in range(end-2, end+1)]
            order = tuple(sorted(range(3), key=lambda k: (r[k], k)))
            counts[order] = counts.get(order, 0)+1
        return -sum((v/60)*np.log(v/60) for v in counts.values())/np.log(6)
    for j in range(63, len(D)):
        if D.index[j]-D.index[j-63] != pd.Timedelta(days=63): continue
        out.iloc[j] = h(j) <= .95 and h(j-1) > .95 and ((c[j]>c[j-20]) if up else (c[j]<c[j-20]))
    return out


def bars(D):
    rows=[]
    for t, d in D.iterrows():
        for hour in range(0,24,4):
            rows.append(dict(t=t+pd.Timedelta(hours=hour),o=d.c,h=d.h,l=d.l,c=d.c,v=d.v/6))
    return pd.DataFrame(rows)


class Checks(unittest.TestCase):
    def test_reference(self):
        for seed, up in itertools.product(range(3), (False,True)):
            D=daily(seed,220)
            pd.testing.assert_series_equal(C.daily_hit(D,C.IP,up),reference(D,up),check_names=False)

    def test_removed_day(self):
        D=daily(size=220).drop(pd.Timestamp('2020-04-01'))
        for up in (False,True):
            pd.testing.assert_series_equal(C.daily_hit(D,C.IP,up),reference(D,up),check_names=False)

    def test_prefixes(self):
        D=daily(size=240); d=bars(D)
        for side in (False,True):
            full=C.signal(d,C.IP,side)
            for n in (721,729,780,923,1101,1308,1439):
                got=C.signal(d.iloc[:n],C.IP,side)
                for k in ('le','se','lx','sx'):
                    np.testing.assert_array_equal(np.asarray(got[k]),np.asarray(full[k])[:n])

    def test_entropy_bounds_and_ties(self):
        D=daily(); D.c=1.
        e=C.entropy(D,60)
        self.assertTrue(e.iloc[:62].isna().all())
        np.testing.assert_allclose(e.iloc[62:],0)
        e=C.entropy(daily(),60).dropna()
        self.assertTrue(((e>=0)&(e<=1.000000000001)).all())

    def test_exact_cross_and_direction(self):
        D=daily(size=100); D.c=np.arange(1.,101.)
        e=pd.Series(.96,index=D.index);e.iloc[70:]=.95
        with patch.object(C,'entropy',return_value=e):
            self.assertEqual(list(np.flatnonzero(C.daily_hit(D,C.IP,True))),[70])
            self.assertFalse(C.daily_hit(D,C.IP,False).any())

    def test_next_day_and_regime(self):
        D=daily(size=150); d=bars(D); hit=pd.Series(False,index=D.index);hit.iloc[130]=True
        for up in (False,True):
            for reg in (-1,0,1):
                with patch.object(C,'daily_hit',return_value=hit),patch.object(C.L1,'coin_regime',return_value=np.full(len(d),reg)):
                    s=C.signal(d,C.IP,up)
                chosen=np.flatnonzero(s['le' if up else 'se'])
                self.assertEqual(chosen.tolist(),[131*6] if reg==(1 if up else -1) else [])
                self.assertFalse(np.asarray(s['se' if up else 'le']).any())

    def test_incomplete_day_not_available(self):
        D=daily(size=150); d=bars(D).drop(index=130*6+2).reset_index(drop=True)
        completed=C.L27._days_v(d)
        self.assertNotIn(D.index[130],completed.index)

    def test_frozen_case_matches_registration(self):
        import json
        from pathlib import Path
        cases=json.loads((Path(__file__).parent/'extra31_screen/longscout_cx1_cases.json').read_text())
        for key,c in cases['cases'].items():
            s=C.CANDIDATES[key]
            self.assertEqual(c['params'],s['params']);self.assertEqual(c['management'],s['mgmt'])
            self.assertEqual(c['side'],s['sides']);self.assertEqual(c['tf'],s['tf'])


if __name__=='__main__':
    unittest.main()
