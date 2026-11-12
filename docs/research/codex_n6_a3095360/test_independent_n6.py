import unittest
from datetime import datetime,timedelta,timezone
from independent_n6 import signal_sets,SYMS

def tape(short=False):
 out={s:{} for s in SYMS};start=datetime(2024,1,1,tzinfo=timezone.utc)
 for i in range(260):
  t=start+timedelta(hours=4*i)
  for s in SYMS:
   c=100. if s=='BTCUSDT' else 100+(-1 if short else 1)*.05*i
   out[s][t]=(c,c+1 if s=='BTCUSDT' else c+2,c-1 if s=='BTCUSDT' else c-2,c)
 for i,c in [(239,80 if short else 120),(240,78 if short else 122),(241,100),(242,75 if short else 125)]:
  t=start+timedelta(hours=4*i);out['BTCUSDT'][t]=(c,c+1,c-1,c)
 return out,start
class Checks(unittest.TestCase):
 def test_first_event_and_btc_exclusion(self):
  d,t=tape();got,_,_=signal_sets(d);self.assertEqual(len(got),14);self.assertEqual({x[1] for x in got},{(t+timedelta(hours=4*240)).isoformat(),(t+timedelta(hours=4*243)).isoformat()});self.assertFalse(any(x[0]=='BTCUSDT' for x in got))
 def test_short_mirror(self):
  d,t=tape(True);got,_,_=signal_sets(d);self.assertEqual(len(got),14);self.assertEqual({x[2] for x in got},{-1})
 def test_exact_touch_does_not_break(self):
  d,t=tape();d['BTCUSDT'][t+timedelta(hours=4*239)]=(100,101,99,101);got,_,_=signal_sets(d);self.assertFalse(any(x[1]==(t+timedelta(hours=4*240)).isoformat() for x in got))
 def test_alt_already_broken_extreme_refused(self):
  d,t=tape();dt=t+timedelta(hours=4*239);d['ETHUSDT'][dt]=(1000,1001,999,1000);got,_,_=signal_sets(d);self.assertNotIn(('ETHUSDT',(t+timedelta(hours=4*240)).isoformat(),1),got)
 def test_opposed_ema_trend_refused(self):
  d,t=tape()
  for i,dt in enumerate(d['ETHUSDT']):
   c=100-.05*i;d['ETHUSDT'][dt]=(c,c+2,c-2,c)
  got,_,_=signal_sets(d);self.assertFalse(any(x[0]=='ETHUSDT' for x in got))
 def test_gap_fails(self):
  d,t=tape();del d['BTCUSDT'][t+timedelta(hours=4*200)]
  with self.assertRaises(ValueError):signal_sets(d)
 def test_future_candles_do_not_change_earlier_candidates(self):
  d,t=tape();full,_,_=signal_sets(d);last=t+timedelta(hours=4*245);prefix={s:{dt:r for dt,r in v.items() if dt<=last} for s,v in d.items()};p,_,_=signal_sets(prefix);self.assertEqual(p,{r for r in full if r[1]<=last.isoformat()})
if __name__=='__main__':unittest.main()
