"""Bounded read-only witness: PR343 accepts uniformly stale complete panel."""
import subprocess,sys,types,json
from pathlib import Path
import pandas as pd
R=Path(r'F:\ZackBot2_data\C1H-20261112\review-branch')
sys.path.insert(0,r'F:\ZackBot2_data\LONGSCOUT-REVIEW-20261114')
import strategies as S
code=subprocess.check_output(['git','show','7cc838c6e0be16a5d7e4be4934573d2b7bfbf503:breadth_thrust_daily.py'],cwd=R).decode('utf-8')
m=types.ModuleType('breadth_review_subject'); exec(compile(code,'<PR343 breadth_thrust_daily.py>','exec'),m.__dict__)
t=pd.date_range('2020-01-01',periods=300,freq='D'); c=pd.DataFrame(100.,index=t,columns=m.PANEL_SYMBOLS); c.iloc[-1]=130.
d=S.indicators(pd.DataFrame(dict(t=t,o=c.iloc[:,0],h=c.iloc[:,0]+1,l=c.iloc[:,0]-1,c=c.iloc[:,0],v=100.)).reset_index(drop=True))
ctx={'panel_c':c}; sig=m.signal(d,m.PARAMS,ctx)
out=dict(head='7cc838c6e0be16a5d7e4be4934573d2b7bfbf503',last_panel_open=str(t[-1]),review_expected_last_closed_daily_open='2026-11-13 00:00:00 UTC',all_32_coins_present=True,panel_accepted=m.panel_of(d,ctx) is not None,last_long_signal=bool(sig['le'].iloc[-1]),last_short_signal=bool(sig['se'].iloc[-1]),scope='Pure strategy guard witness, not an exchange test or end-to-end live order. Engine.candles source only removes unclosed bars; it does not reject uniformly stale last closed bar. Require explicit live decision cutoff, preserving historical scoring.')
assert out['panel_accepted'] and out['last_long_signal']
O=Path(r'F:\ZackBot2_data\DAILY-TF-01-REVIEW-20261114'); O.mkdir(exist_ok=True,parents=True)
(O/'stale_panel_witness.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
print(json.dumps(out,indent=2))
