import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_downside_gap_side_by_side_white_c253 as C
def tape(active=True):
 x=np.arange(650.);c=150-.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(42,len(x),47):
  b=c[i];o[i-2:i+1]=[b+2,b+.1,b+.15];c[i-2:i+1]=[b+1,b+.7,b+.75];h[i-2:i+1]=[b+2.1,b+.8,b+.85];l[i-2:i+1]=[b+.9,b,b+.05]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c+1 if active else d.c-1;return d
def signal(d):return S.sig_downside_gap_side_by_side_white_short_1h(d,S.DOWNSIDEWHITELINES1H,{})
def pattern(o,h,l,c,a=None):return S._downside_gap_side_by_side_white(pd.Series(o),pd.Series(h),pd.Series(l),pd.Series(c),pd.Series(a or [1.,1.,1.])).iloc[-1]
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['se'].sum()>0
def test_trend_filter():assert not signal(tape(False))['se'].any()
def test_geometry():assert pattern([12.,10.1,10.15],[12.1,10.8,10.85],[10.9,10.,10.05],[11.,10.7,10.75])
def test_bearish_lead_required():assert not pattern([11.,10.1,10.15],[12.1,10.8,10.85],[10.9,10.,10.05],[12.,10.7,10.75])
def test_true_range_gap_required():assert not pattern([12.,10.8,10.85],[12.1,11.1,11.15],[10.9,10.7,10.75],[11.,11.,11.05])
def test_both_white_lines_hold_gap():assert not pattern([12.,10.1,10.15],[12.1,10.8,11.],[10.9,10.,10.05],[11.,10.7,10.75])
def test_matched_opens_required():assert not pattern([12.,10.1,10.4],[12.1,10.8,11.1],[10.9,10.,10.3],[11.,10.7,11.])
def test_comparable_bodies_required():assert not pattern([12.,10.1,10.15],[12.1,10.8,11.15],[10.9,10.,10.05],[11.,10.7,11.05])
def test_cost():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['se'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['se'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
