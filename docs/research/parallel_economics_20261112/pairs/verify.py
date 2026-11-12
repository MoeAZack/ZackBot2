from pathlib import Path
import pandas as pd,numpy as np
p=Path(__file__).parent
ns={}
exec((p/'runner.py').read_text(encoding='utf-8-sig').split('\ndata=load()')[0],ns)
a=pd.DataFrame({'t':np.arange(1000)*3600000,'c':np.exp(5+np.arange(1000)*.0002+np.sin(np.arange(1000)/13)*.02),'qv':np.full(1000,50000.)})
b=pd.DataFrame({'t':np.arange(1000)*3600000,'c':np.exp(4+np.arange(1000)*.00013+np.sin(np.arange(1000)/17)*.01),'qv':np.full(1000,50000.)})
s=ns['sig'](a,b); aa=a.copy(); aa.loc[850,'c']*=1.1; changed=ns['sig'](aa,b)
assert np.isclose(s.loc[850,'beta'],changed.loc[850,'beta']) and np.isclose(s.loc[850,'sd'],changed.loc[850,'sd'])
assert s.loc[850,'z']!=changed.loc[850,'z']
pd.testing.assert_frame_equal(s.iloc[:851],ns['sig'](a.iloc[:851],b.iloc[:851]),check_exact=False)
for d in [p,p/'filter_sensitivity']:
 for f in d.glob('*trades.csv'):
  r=pd.read_csv(f)
  assert np.allclose(r.gross-r.cost-r.funding,r.net,atol=1e-8)
  assert (r.hours<=48).all()
print('PASS: prefix invariance; signal-bar beta/sd lag and z reaction; net accounting on 12 ledgers; maximum hold.')
print('LIMIT: no synthetic execution fixture or independent simulator; risk is theoretical, filters retrospective.')
