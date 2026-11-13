import numpy as np,pandas as pd,pytest
from spb_signals import signal,decision,spot_row

def panel():
 ix=pd.date_range('2024-01-01',periods=1000,freq='h'); v=np.sin(np.arange(1000)*.3)*.0001
 return pd.DataFrame({'c':100.,'qv':1e6},index=ix),pd.DataFrame({'c':100*np.exp(v),'qv':1e6},index=ix)
def test_first_cross_and_prior_only():
 s,f=panel(); f.iloc[800,f.columns.get_loc('c')]=100*np.exp(.004); f.iloc[801,f.columns.get_loc('c')]=100*np.exp(.004)
 sg,z,b,mean=signal(s,f); assert sg.iloc[800]==-1 and sg.iloc[801]==0
 old=mean.iloc[800]; f.iloc[800,f.columns.get_loc('c')]=100*np.exp(.008); assert signal(s,f)[3].iloc[800]==old
 assert z.iloc[799]<3
def test_gap_fail_closed():
 s,f=panel(); s.iloc[790,s.columns.get_loc('c')]=np.nan; f.iloc[800,f.columns.get_loc('c')]=101
 assert signal(s,f)[0].iloc[800]==0

def test_delayed_decision_atr_and_gaps():
 t=pd.date_range('2025-01-01',periods=5,freq='h').to_numpy(); a={'t':t,'atr':np.array([1.,2.,9.,4.,5.])}; d=decision(a,t[1],-1)
 assert d[0]==t[3] and d[2]==2 and d[3]==6
 a['t']=np.delete(t,2); a['atr']=np.array([1.,2.,4.,5.]); assert decision(a,t[1],1) is None

def test_spot_conversion_and_price():
 ms=1735689600000; row=[str(ms*1000),'0','0','0','100','0',str((ms+3600000)*1000-1),'10']
 assert spot_row(row,1000)==(ms,100.,10.)
 row[4]='0'
 with pytest.raises(ValueError): spot_row(row,1000)
 row[4]='100'; row[6]=str((ms+3600000)*1000)
 with pytest.raises(ValueError): spot_row(row,1000)

def test_real_simulator_fill_and_risk():
 import sys
 sys.path.insert(0,'F:/ZackBot2_data/SPOTFLOW-D01-20261112/harness/research/rsr1h')
 import run_rsr1h as R
 t=pd.date_range('2025-01-01',periods=20,freq='h').to_numpy(); a={'t':t,'atr':np.arange(20,dtype=float)+1,'rule':{'step':.001,'min_qty':.001,'min_notional':1}}
 for k in ('o','h','l','c'): a[k]=np.full(20,100.)
 a['o'][3]=111.; d=decision(a,t[1],1); lot=R.run_lot(a,d[2],d[1],d[3],15,'causal')
 assert lot['t_in']==pd.Timestamp(t[3]) and lot['e_ref']==111. and abs(lot['risk']/lot['qty']-6)<1e-9
