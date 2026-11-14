import io,json,subprocess
from pathlib import Path
import pandas as pd
R=Path('F:/ZackBot2_data/LONGSCOUT-V5-REVIEW-20261114');H='f9a914babc53dd2c2ad1eac438a33c26fcdc0b88'
fresh=set(json.loads(Path('C:/Dev/ZackBot_research_cache/longscout_fresh_20261114/selected.json').read_text())['selected'])
def blob(p):return subprocess.check_output(['git','--git-dir='+str(R/'objects.git'),'show',H+':'+p])
out={}
for case in ['s5_high_beta_btc_down_4h','r5_ichimoku_cloud_4h']:
    base=f'docs/research/longscout_v5/{case}/{case}'
    data=pd.read_csv(io.BytesIO(blob(base+'_all_orders_trades.csv.gz')),compression='gzip'); data=data[data.scen=='path'].copy();data['t_in']=pd.to_datetime(data.t_in)
    orders=[]
    for i in range(40):
        t=data[data.order==i];by=t.groupby('sym').pnl15.sum();best=by.idxmax();ex=t[t.sym!=best]
        mean=lambda x,col='pnl15':float(x[col].mean()/8) if len(x) else None
        orders.append({'n':len(t),'n_early':int((t.t_in<pd.Timestamp('2025-01-01')).sum()),'n_late':int((t.t_in>=pd.Timestamp('2025-01-01')).sum()),'meanR15':mean(t),'meanR23':mean(t,'pnl23'),'early':mean(t[t.t_in<pd.Timestamp('2025-01-01')]),'late':mean(t[t.t_in>=pd.Timestamp('2025-01-01')]),'ex_best':mean(ex),'fresh':mean(t[t.sym.isin(fresh)]),'extra':mean(t[~t.sym.isin(fresh)])})
    m=pd.DataFrame(orders).median(numeric_only=True).to_dict(); gates={'trades':m['n']>=100,'meanR15':m['meanR15']>0,'meanR23':m['meanR23']>=0,'early':m['early']>0,'late':m['late']>0,'ex_best':m['ex_best']>0,'fresh':m['fresh']>0,'extra':m['extra']>0,'order0_30_each':orders[0]['n_early']>=30 and orders[0]['n_late']>=30}
    out[case]={'medians':m,'gates':gates,'failed':[k for k,v in gates.items() if not v],'orders':len(orders)}
(R/'independent_gate_arithmetic.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
