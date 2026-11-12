import importlib.util,pathlib,pandas as pd,numpy as np
p=pathlib.Path(r'F:\ZackBot2_data\FUNDING-CANDIDATES-20261112'); spec=importlib.util.spec_from_file_location('research',p/'run.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
# Controlled 1h candles; both hit => stop, gap exits at open, signed funding, costs.
def dframe(rows):
 d=pd.DataFrame(rows,columns=['o','h','l','c']); d['t']=pd.date_range('2024-01-01',periods=len(d),freq='h'); d['atr']=1.0; return d
f=pd.DataFrame({'t':[pd.Timestamp('2024-01-01 02:00:00.001')],'mark':[100.0],'r':[.001]})
d=dframe([(100,101,99,100),(100,107,96,100),(100,101,99,100)])
a=m.lot(d,f,(.001,.001,0),0,1,.0005,False); b=m.lot(d,f,(.001,.001,0),0,-1,.0005,False)
assert a['why']=='stop' and b['why']=='stop' and a['pnl']<0 and b['pnl']<0
# Market gaps on following candle use open and cannot exceed position cap risk at entry.
d=dframe([(100,101,99,100),(100,101,99,100),(90,91,89,90)])
a=m.lot(d,f,(.001,.001,0),0,1,.0005,False); assert a['why']=='stop_gap' and a['risk']<=8 and a['exit']==pd.Timestamp('2024-01-01 02:00')
# One settlement inside a time-capped lot has opposite directional funding signs.
d=dframe([(100,100.1,99.9,100)]*27)
a=m.lot(d,f,(.001,.001,0),0,1,.0005,False); b=m.lot(d,f,(.001,.001,0),0,-1,.0005,False)
assert a['settlements']==1 and a['funding']<0 and b['funding']>0 and abs(a['funding']+b['funding'])<1e-12
print('HOSTILE TESTS PASSED: long/short stop collision, gap, risk floor, one native-rate debit/credit')
