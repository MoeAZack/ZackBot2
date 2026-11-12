import pathlib, sys, unittest
import numpy as np
import pandas as pd
from spotflow_signals import signal, prior_complete_days_elig, wilder_atr

class SignalTests(unittest.TestCase):
 def setUp(self):
  ix=pd.date_range('2024-01-01',periods=800,freq='h')
  self.p=pd.DataFrame({'o':100.,'h':100.5,'l':99.5,'c':100.,'v':1000.},index=ix)
  self.b=self.p.copy(); self.sp=pd.DataFrame({'qv':500000.,'tbqv':300000.},index=ix); self.pe=self.sp.copy(); self.pe.tbqv=200000.
  self.p.iloc[-3:,self.p.columns.get_loc('c')]=[99.,98.,97.]
  self.p['h']=self.p.c+.5; self.p['l']=self.p.c-.5
 def test_long_and_direction(self):
  s,_=signal(self.sp,self.pe,self.p,self.b); self.assertEqual(s.iloc[-1],1)
  rev=self.p.copy(); rev.c=200-self.p.c; rev.h=rev.c+.5; rev.l=rev.c-.5
  s,_=signal(self.pe,self.sp,rev,self.b); self.assertEqual(s.iloc[-1],-1)
 def test_zero_qv_missing_and_no_lookahead(self):
  s,_=signal(self.sp,self.pe,self.p,self.b); sp=self.sp.copy(); sp.iloc[-1,sp.columns.get_loc('qv')]=0
  t,_=signal(sp,self.pe,self.p,self.b); self.assertEqual(t.iloc[-1],0); pd.testing.assert_series_equal(s.iloc[:-1],t.iloc[:-1])
  p=self.p.copy(); p.iloc[-2,p.columns.get_loc('c')]=np.nan
  self.assertEqual(signal(self.sp,self.pe,p,self.b)[0].iloc[-1],0)
 def test_btc_skip(self):
  b=self.b.copy(); b.iloc[-1,b.columns.get_loc('c')]=102
  self.assertEqual(signal(self.sp,self.pe,self.p,b)[0].iloc[-1],0)
 def test_prior_daily_not_hourly(self):
  q=self.sp.qv.copy(); self.assertTrue(prior_complete_days_elig(q).iloc[-1]); q.iloc[-24*10]=np.nan
  self.assertFalse(prior_complete_days_elig(q).iloc[-1]); self.assertFalse(prior_complete_days_elig(self.sp.qv*0.5).iloc[-1])
  historical_perp=self.pe.copy(); historical_perp.iloc[-100,historical_perp.columns.get_loc('qv')]=np.nan
  self.assertEqual(signal(self.sp,historical_perp,self.p,self.b)[0].iloc[-1],1)
 def test_atr_matches_frozen_indicator(self):
  local=pathlib.Path(__file__).resolve().parent/'harness'
  sys.path.insert(0,str(local if (local/'strategies.py').exists() else pathlib.Path('F:/ZackBot2_data/X2-WIDE-MINIMAL-REVIEW-20261112')))
  import strategies
  f=self.p.reset_index().rename(columns={'index':'t'})
  expected=strategies.indicators(f).atr.to_numpy()
  np.testing.assert_allclose(wilder_atr(self.p).to_numpy(),expected,rtol=1e-13,atol=1e-13)
 def test_last_signal_refused_and_terminal_close(self):
  root=pathlib.Path(__file__).resolve().parent/'harness'
  if not root.exists(): self.skipTest('isolated harness required')
  sys.path.insert(0,str(root/'research/rsr1h')); import run_rsr1h as r
  t=pd.date_range('2026-09-27 22:00',periods=2,freq='h').values
  a={'t':t,'o':np.full(2,100.),'h':np.full(2,100.1),'l':np.full(2,99.9),'c':np.full(2,100.),'rule':{'step':.001,'min_qty':.001,'min_notional':5.}}
  self.assertEqual(r.run_lot(a,1,1,2.,15,'causal')['refused'],'missing_next_hour')
  lot=r.run_lot(a,0,1,2.,15,'causal')
  self.assertEqual(lot['why'],'TERMINAL_OPEN'); self.assertEqual(lot['t_out'],pd.Timestamp('2026-09-28 00:00'))

if __name__=='__main__': unittest.main()
