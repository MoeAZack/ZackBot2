import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_fractal_chaos_bands_c168 as C
def tape(active=True):
 x=np.arange(500.);c=100+.05*x+3*np.sin(x/10)+(np.where((x%80)==60,8,0) if active else 0) if active else 180-.1*x;o=np.r_[c[0],c[:-1]];d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.e200;return d
def signal(d):return S.sig_fractal_chaos_breakout_long_1h(d,S.FCB1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_down():assert not signal(tape(False))['le'].any()
def test_confirmation():
 h=pd.Series([1.,2.,5.,2.,1.,6.]);l=pd.Series([3.,2.,0.,2.,3.,3.]);u,z=S._fractal_chaos_bands(h,l);assert np.isnan(u.iloc[3]) and u.iloc[4]==5 and z.iloc[4]==0
def test_scale():
 d=tape();a=S._fractal_chaos_bands(d.h,d.l);b=S._fractal_chaos_bands(d.h*2,d.l*2);assert all(np.allclose(x.dropna()*2,y.dropna()) for x,y in zip(a,b))
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'h']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
