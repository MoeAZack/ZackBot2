"""Fresh-process failed-install deadline witness, no engine instance or network."""
import sys,pathlib,types,json
sys.dont_write_bytecode=True
root=pathlib.Path('F:/ZackBot2_data/R6-PAPER-01-REVIEW-20261114');sys.path.insert(0,str(root))
import pandas as pd,numpy as np
import r6_regime_flip as R,r6_paper_overlay as O,strategies as S
R.unregister()
d=pd.DataFrame({'t':pd.date_range(end='2026-11-14 00:00:00',periods=1500,freq='4h'),'c':np.linspace(200,100,1500)})
entry=d.t.iloc[-1]-121*R.STEP
lot={'sleeve':'EX14','key_strategy':R.KEY,'symbol':'AAVEUSDT','side':'SHORT','opened':entry.isoformat(),'r6_entry_bar':entry.isoformat()}
e=types.SimpleNamespace(live=False,cfg={'MODE':'paper'},trade=types.SimpleNamespace(base='https://demo-fapi.binance.com'),data=types.SimpleNamespace(_once=lambda *a:None),rules={s:{} for s in R.SYMBOLS if s!='ZRXUSDT'},rules_meta={},S={'SLEEVES':[O.sleeve(False)]},state={'lots':{'lot':lot}},_kc={('AAVEUSDT','4h'):(d,0)})
try:O.install(e)
except O.R6InstallError as ex:why=str(ex)
out=S.STRATEGIES[R.KEY]['fn'](d,{}, {})
result={'head':'c337bed948573fffe62b345812735c380501150d','install_error':why,'exits_only':S.STRATEGIES[R.KEY] is R._EXITS_ONLY,'context_resolver_present':R._CTX['resolve'] is not None,'context_lot_entry_present':R._CTX['lot_entry'] is not None,'last_raw_short_exit':bool(R.raw_signals(d)['sx'].iloc[-1]),'last_exit_after_deadline':bool(out['sx'].iloc[-1]),'decision_bars_after_entry':121,'expected_short_exit':True}
(root/'r2_fallback_witness.json').write_text(json.dumps(result,indent=2),encoding='utf8',newline='\n');print(json.dumps(result,indent=2));R.unregister()
