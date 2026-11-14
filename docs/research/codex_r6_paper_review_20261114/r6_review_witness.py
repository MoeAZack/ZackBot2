"""Independent PR350 review witnesses. No runtime imports or exchange calls."""
import os, sys, tempfile, json, pathlib, types, threading, hashlib
ROOT=pathlib.Path('F:/ZackBot2_data/R6-PAPER-01-REVIEW-20261114')
os.environ['LOCALAPPDATA']=tempfile.mkdtemp(prefix='r6_review_app_')
sys.dont_write_bytecode=True
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import r6_regime_flip as R, r6_paper_overlay as O, strategies as S
import app
result={}
R.register_for_paper('paper','https://demo-fapi.binance.com',lambda d:'AAVEUSDT',lambda *a:None)
O.allow_exact_list(app)
for name, change in [('foreign_slot',dict(id='EX15',symbols=['AAVEUSDT'])),('short_list',dict(symbols=['AAVEUSDT'])),('larger_risk',dict(risk=.048)),('three_positions',dict(max_pos=3))]:
    try:
        sl=app.validate_sleeve(dict(O.sleeve(True),**change),0)
        result[name]={'accepted':True,'id':sl['id'],'risk':sl['risk'],'max_pos':sl['max_pos'],'n_symbols':len(sl['symbols'])}
    except Exception as ex: result[name]={'accepted':False,'error':str(ex)}
e=types.SimpleNamespace(live=False,cfg={'MODE':'paper'},trade=types.SimpleNamespace(base='https://demo-fapi.binance.com'),data=types.SimpleNamespace(_once=lambda *a:None),rules={s:{} for s in R.SYMBOLS},rules_meta={},S={'SLEEVES':[O.sleeve(True)]},state={'lots':{}},lock=threading.RLock())
e.S['SLEEVES'][0]['risk']=.048
result['guard_after_risk_doubled']=O.entry_guard(e)()
e.rules.pop('AAVEUSDT')
before=S.STRATEGIES[R.KEY]
try: O.install(e)
except Exception as ex: result['failed_reinstall']={'error':str(ex),'kept_active_entry':S.STRATEGIES[R.KEY] is before,'exits_only':S.STRATEGIES[R.KEY] is R._EXITS_ONLY,'entry_guard':O.entry_guard(e)()}
R.unregister()
R.register_for_paper('paper','https://demo-fapi.binance.com',lambda d:'AAVEUSDT',lambda *a:None)
e.S={'SLEEVES':[]}; e.rules={s:{} for s in R.SYMBOLS}
seen=[]
def save(): seen.append({'slot_visible':bool(e.S['SLEEVES']),'pacer_installed':getattr(e.data,'_r6_paced',False)})
e.save_settings=save
O.add_sleeve(e,False)
result['save_before_pacer']=seen
result['clock_exit']=[{'entry_delay_min':entry,'cycle_delay_min':delay,'age_at_prior_close':120+(delay-entry)/240,'early_exit':120+(delay-entry)/240>=120.5} for entry,delay in [(0,20),(20,0),(0,120),(20,140)]]
import pandas as pd
R.unregister()
close=pd.Timestamp('2026-11-14 00:00:00')
d=pd.DataFrame({'t':pd.date_range(end=close-pd.Timedelta(hours=4),periods=768,freq='4h'),'c':100.})
now=[close.timestamp()+60]
def fetch(*args):
    now[0]=close.timestamp()+1260
    return pd.DataFrame({'t':pd.date_range(start=close-pd.Timedelta(days=32),end=close-pd.Timedelta(hours=1),freq='1h'),'qv':1000000.})
R.register_for_paper('paper','https://demo-fapi.binance.com',lambda _: 'AAVEUSDT',fetch,clock=lambda:now[0])
result['slow_fetch_gate']={'gate_result':R.gate(d),'returned_minutes_after_close':(now[0]-close.timestamp())/60}
print(json.dumps(result,indent=2))
(ROOT/'independent_witness.json').write_text(json.dumps(result,indent=2),encoding='utf8')
O.restore_panel(app)
R.unregister()
