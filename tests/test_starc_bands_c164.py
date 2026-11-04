import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_starc_bands_c164 as C
def tape(active=True):
 x=np.arange(500.);c=100+.05*x+3*np.sin(x/10)+(np.where((x%80)==60,8,0) if active else 0) if active else 180-.1*x;o=np.r_[c[0],c[:-1]];d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.e200;return d
def signal(d):return S.sig_starc_band_breakout_long_1h(d,S.STARC1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_down():assert not signal(tape(False))['le'].any()
def test_formula():
 d=tape();prev=d.c.shift();tr=pd.concat([(d.h-d.l).abs(),(d.h-prev).abs(),(d.l-prev).abs()],axis=1).max(axis=1);atr=tr.ewm(alpha=1/15,adjust=False,min_periods=15).mean();mid=d.c.rolling(6,min_periods=6).mean();got=S._starc_bands(d.h,d.l,d.c);assert np.allclose(got[0].dropna(),mid.dropna()) and np.allclose(got[1].dropna(),(mid+2*atr).dropna())
def test_scale():
 d=tape();a=S._starc_bands(d.h,d.l,d.c);b=S._starc_bands(d.h*2,d.l*2,d.c*2);assert all(np.allclose(x.dropna()*2,y.dropna()) for x,y in zip(a,b))
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
