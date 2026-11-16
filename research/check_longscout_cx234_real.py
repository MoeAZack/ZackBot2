"""Own daily aggregation/regime, reference signals and real prefix checks.

Usage: PYTHONPATH=research:. python research/check_longscout_cx234_real.py DATA_DIR OUTPUT_JSON
Reference functions are independent scalar loops in the synthetic test file.
No execution/admission reproduction or profitability inference is claimed.
"""
import sys,json
from pathlib import Path
import numpy as np
import pandas as pd
import longscout_cx2 as A
import longscout_cx3 as B
import longscout_cx4 as C
from test_longscout_cx234 import ref_cusum,ref_coupling


def main():
    rows=[]
    for coin in ['AAVEUSDT','BCHUSDT','UNIUSDT']:
        h=pd.read_csv(Path(sys.argv[1])/(coin+'_1h.csv'))
        h.t=pd.to_datetime(h.t,unit='ms') if pd.api.types.is_numeric_dtype(h.t) else pd.to_datetime(h.t)
        g=h.set_index('t').resample('4h')
        d=g.agg(dict(o='first',h='max',l='min',c='last',v='sum'))
        d=d[g.size()==4].dropna().reset_index()
        groups=d.groupby(d.t.dt.floor('D'))
        D=groups.agg(h=('h','max'),l=('l','min'),c=('c','last'),v=('v','sum'))[groups.size()==6]
        ma=D.c.rolling(100,min_periods=100).mean();slope=ma-ma.shift(20)
        regime=np.where((D.c>ma)&(slope>0),1,np.where((D.c<ma)&(slope<0),-1,0))
        regime=np.where(ma.isna()|slope.isna(),np.nan,regime)
        events2=ref_cusum(D,A.IP)
        q=[];rets=[]
        for day in D.index:
            x=groups.get_group(day);r=np.array([np.log(row.c/row.o) for row in x.itertuples()]);e=sum(r*r)
            q.append(max(r*r)/e if e else 0.);rets.append(sum(r))
        q=np.asarray(q);rets=np.asarray(rets);rho=ref_coupling(D,C.IP)
        for M in (A,B,C):
            for up in (False,True):
                event=np.zeros(len(D),bool)
                for j in range(len(D)):
                    if M is A:event[j]=events2[j,0 if up else 1]
                    elif M is B:
                        event[j]=j>0 and D.index[j]-D.index[j-1]==pd.Timedelta(days=1) and q[j]>=.6 and q[j-1]<.6 and (rets[j]>0 if up else rets[j]<0)
                    elif j>=62 and D.index[j]-D.index[j-62]==pd.Timedelta(days=62):
                        event[j]=(rho[j]>.3 and rho[j-1]<=.3 and D.c.iloc[j]>D.c.iloc[j-20]) if up else (rho[j]<-.3 and rho[j-1]>=-.3 and D.c.iloc[j]<D.c.iloc[j-20])
                event &= regime==(1 if up else -1)
                expected=pd.DatetimeIndex(D.index[event])+pd.Timedelta(days=1)
                got=M.signal(d,M.IP,up);key='le' if up else 'se'
                actual=pd.DatetimeIndex(d.t[np.asarray(got[key])])
                expected=expected[expected.isin(pd.DatetimeIndex(d.t))]
                assert actual.equals(expected),(coin,M.__name__,up,len(actual),len(expected))
                picks=np.flatnonzero(np.asarray(got[key]))
                # Include actual signal cutoffs as well as partial-day/end cutoffs.
                cutoffs=sorted(set([len(d)-1,len(d)-4]+[int(i)+1 for i in picks[::max(1,len(picks)//3)][:3]]))
                for end in cutoffs:
                    prefix=M.signal(d.iloc[:end],M.IP,up)
                    for k in ('le','se','lx','sx'):
                        np.testing.assert_array_equal(np.asarray(prefix[k]),np.asarray(got[k])[:end])
                rows.append(dict(coin=coin,module=M.__name__,side='long' if up else 'short',signals=len(actual),prefix_checks=len(cutoffs),matches=True))
    Path(sys.argv[2]).write_text(json.dumps(dict(independent_signal_checks=rows,signal_mismatches=0,prefix_mismatches=0,execution_replay=False),indent=2)+'\n')
    print('18 independent real-data signal comparisons passed;',sum(x['prefix_checks'] for x in rows),'real prefix comparisons passed')


if __name__=='__main__':main()
