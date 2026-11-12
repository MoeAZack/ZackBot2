import sys, ast, json, hashlib
from pathlib import Path
import numpy as np, pandas as pd
P=Path(r'F:\ZackBot2_data\RSR-INDEPENDENT-20261112'); root=Path(r'F:\ZackBot2_data\X2-WIDE-MINIMAL-REVIEW-20261112'); ref=Path(r'F:\ZackBot2_data\RSR-REVIEW-20261112\run_rsr1h.py'); data=Path(r'C:\Dev\ZackBot_research_cache\h5_wide_d01_20261112\clean')
assert hashlib.sha256((P/'entries.csv').read_bytes()).hexdigest()=='277d85875d0af49dc138d7d86836e1ac07d051c5feb9589d55b03cd2529250ec'
sys.path.insert(0,str(root/'research'/'x2wide')); import run_x2wide as X
D=X.D; syms=list(D.ORIG)+list(D.EXTRA); assert len(syms)==40
raw={s:D.load(str(data),s,{}) for s in syms}
tree=ast.parse(ref.read_text(encoding='utf8')); names={'panel','loo_market','_roll_prior','features','signals','prepare'}; nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]; assert len(nodes)==6
ns=dict(np=np,pd=pd,WIN=168,MIN_OBS=120,MIN_CONTRIB=8,Z_TH=3.,MKT_SKIP=.015,HOUR=X.HOUR,STOP_ATR=3.,X=X); exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ref),'exec'),ns)
coins,_,_=ns['prepare'](raw,syms)
author={(pd.Timestamp(d-X.HOUR,tz='UTC').isoformat(),s,'LONG' if side==1 else 'SHORT') for s in syms for d,side,i,dist in coins[s]['dec']}
df=pd.read_csv(P/'entries.csv'); df.signal_open_utc=pd.to_datetime(df.signal_open_utc,utc=True); lo=pd.Timestamp(X.T0,tz='UTC'); hi=pd.Timestamp(X.T1,tz='UTC'); df=df[(df.signal_open_utc>=lo)&(df.signal_open_utc<=hi)]; independent={(t.isoformat(),s,d) for t,s,d in zip(df.signal_open_utc,df.symbol,df.side)}
report={'independent_count':len(independent),'reference_full_prepare_count':len(author),'independent_only_count':len(independent-author),'reference_only_count':len(author-independent),'independent_only_sample':sorted(independent-author)[:10],'reference_only_sample':sorted(author-independent)[:10],'bounds_utc':[lo.isoformat(),hi.isoformat()]}; (P/'full_loader_comparison.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report))
