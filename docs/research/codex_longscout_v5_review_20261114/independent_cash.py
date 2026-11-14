"""Recompute first five published order-zero closes without importing research or engine modules."""
import io,json,math,subprocess
from pathlib import Path
import numpy as np
import pandas as pd
R=Path('F:/ZackBot2_data/LONGSCOUT-V5-REVIEW-20261114'); F=Path('C:/Dev/ZackBot_research_cache/longscout_fresh_20261114/clean'); E=Path('C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean')
H='f9a914babc53dd2c2ad1eac438a33c26fcdc0b88'; SRC='d88221334d7b6a2cc32141406e77146ee553ca85'
def blob(commit,path):return subprocess.check_output(['git','--git-dir='+str(R/'objects.git'),'show',commit+':'+path])
rules=json.loads(blob(SRC,'data/exchange_rules_testnet.json'))['symbols']
fresh=set(json.loads(Path('C:/Dev/ZackBot_research_cache/longscout_fresh_20261114/selected.json').read_text())['selected'])
def frame(sym):
    d=pd.read_csv((F if sym in fresh else E)/f'{sym}_1h.csv');d.t=pd.to_datetime(d.t,unit='ms')
    g=d.set_index('t').resample('4h');f=g.agg({'o':'first','h':'max','l':'min','c':'last'});f=f[g.size()==4].dropna().reset_index()
    c=f.c; true=np.maximum(f.h-f.l,np.maximum((f.h-c.shift()).abs().fillna(0),(f.l-c.shift()).abs().fillna(0)))
    f['atr']=true.ewm(alpha=1/14,adjust=False).mean();f['kij']=(f.h.rolling(26).max()+f.l.rolling(26).min())/2
    return f
def floored(q,step):
    if not step:return q
    dec=max(0,-int(math.floor(math.log10(step)))) if step<1 else 0
    return round(math.floor(q/step+1e-9)*step,dec)
results={}
for case in ['s5_high_beta_btc_down_4h','r5_ichimoku_cloud_4h']:
    ref=pd.read_csv(io.BytesIO(blob(H,f'docs/research/longscout_v5/{case}/{case}_order0_path_trades.csv'))).head(5)
    cash=1e7; records=[]
    for r in ref.itertuples():
        f=frame(r.sym); idx=np.flatnonzero(f.t.eq(pd.Timestamp(r.t_in)))[0]; side=int(r.side); entryref=f.o.iloc[idx]; entry=entryref*(1+side*.0002)
        dist=2.5*f.atr.iloc[idx-1]; qty=floored(min((cash*8/1e7)/dist,(cash*3333.33/1e7)/entry),rules.get(r.sym,{}).get('step'))
        stop=entry-side*dist; target=entry+side*3*dist; funding=0.; exit_ref=None
        for i in range(idx,len(f)):
            row=f.iloc[i];funding+=qty*row.c*.00005
            if side*(row.o-stop)<=0: exit_ref=row.o; why='stop';break
            if side*((row.l if side==1 else row.h)-stop)<=0:exit_ref=stop;why='stop';break
            if case.startswith('s5_') and side*((row.h if side==1 else row.l)-target)>=0:exit_ref=target;why='tp';break
            signal=(row.c<row.kij if side==1 else row.c>row.kij) if case.startswith('r5_') else False
            if signal or i-idx >= (120 if case.startswith('r5_') else 60):exit_ref=row.c;why='signal' if signal else 'time';break
        assert exit_ref is not None
        px=exit_ref*(1-side*.0002);pnl=side*(px-entry)*qty-(entry+px)*qty*.0005-funding;turnover=(entryref+exit_ref)*qty
        out={'sym':r.sym,'entry':str(r.t_in),'published_exit':str(r.t_out),'reconstructed_exit':str(f.t.iloc[i]),'published_reason':r.why,'reconstructed_reason':why,'qty':qty,'pnl7':pnl,'published_pnl7':r.pnl7,'pnl15':pnl-turnover*.0008,'published_pnl15':r.pnl15,'absolute_error':abs(pnl-r.pnl7),'unknown_rule':r.sym not in rules}
        records.append(out);cash+=pnl
    results[case]=records
(R/'independent_cash_sample.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
