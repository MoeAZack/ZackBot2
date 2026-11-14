"""Descriptive overlap of exact, independently reproduced frozen journals, no joint-book claim."""
from pathlib import Path
import io,json,subprocess
import pandas as pd
import numpy as np
REPO=Path('F:/ZackBot2_data/C1H-20261112/review-branch')
def tape(head,version,key):
    p=f'docs/research/longscout_{version}/{key}/{key}_all_orders_trades.csv.gz'
    b=subprocess.check_output(['git','show',head+':'+p],cwd=REPO)
    t=pd.read_csv(io.BytesIO(b),compression='gzip')
    return t[t.scen=='path'].copy()
a=tape('10486f35dd513c51b375b3b72cfe5864763a7b39','v6','r6_regime_flip_4h')
b=tape('b6f82edb0be9a134aefb74ec1b58366d269628f4','v17','r17_ema50_envelope_regime_4h')
tl=pd.date_range(start=min(pd.to_datetime(a.t_in).min(),pd.to_datetime(b.t_in).min()),end=max(pd.to_datetime(a.t_out).max(),pd.to_datetime(b.t_out).max()),freq='4h')
for t in (a,b):
    t['i_in']=tl.get_indexer(pd.to_datetime(t.t_in));t['i_out']=tl.get_indexer(pd.to_datetime(t.t_out))
    assert (t.i_in>=0).all() and (t.i_out>=0).all()
n=len(tl)
rows=[]
for k in range(40):
    aa=a[a.order==k];bb=b[b.order==k]
    sa=np.full(n,'',dtype=object);sb=np.full(n,'',dtype=object)
    da=np.zeros(n,dtype=int);db=np.zeros(n,dtype=int)
    for t,s,d in [(aa,sa,da),(bb,sb,db)]:
        for r in t.itertuples():
            sl=slice(r.i_in,r.i_out+1);s[sl]=r.sym;d[sl]=r.side
    both=(da!=0)&(db!=0);same=both&(sa==sb)&(da==db)
    starts=bb.i_in.to_numpy()
    overlaps=int(sum(sa[r.i_in]==r.sym and da[r.i_in]==r.side for r in bb.itertuples()))
    rows.append(dict(order=k,r6_closed_lots=len(aa),r17_closed_lots=len(bb),r6_active_bars=int((da!=0).sum()),r17_active_bars=int((db!=0).sum()),both_active_bars=int(both.sum()),same_coin_side_bars=int(same.sum()),same_side_when_both=float((both&(da==db)).sum()/both.sum()),r17_time_same_coin_side=float(same.sum()/(db!=0).sum()),r17_entries_while_r6_same_coin_side=overlaps,r17_entry_fraction_same_coin_side=float(overlaps/len(bb))))
df=pd.DataFrame(rows)
out=dict(method='Closed-lot holding intervals include entry and exit bars on the common BTC timeline. Terminal open lots excluded; no shared-account admission or P&L inferred. Native supplied journals used only after independent R6/R17 signal and execution reproductions.',orders=40,medians=df.drop(columns='order').median().to_dict(),order0=rows[0])
p=Path('F:/ZackBot2_data/R17-REVIEW-20261114');(p/'overlap.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf8',newline='\n');df.to_csv(p/'overlap_orders.csv',index=False)
print(json.dumps(out,indent=2))
