import unittest
from overlay_sim import simulate

def row(u,s='a',coin='BTC',entry='2024-05-01T00:00:00Z',exit='2024-05-01T05:00:00Z',R=1,side=1):
 return dict(uniqueid=u,sleeve=s,symbol=coin,side=side,entry_utc=entry,exit_utc=exit,net_R_at_cost=R,risk_fraction=.002 if s=='acct4' else .004,max_positions=1 if s=='acct4' else 2)
def run(rows,policy='baseline',**kw): return simulate(rows,['a','b','c','d','e','f','g','acct4'],policy,'fixed',**kw)
def rec(out): return {x['uniqueid']:x for x in out['records']}
class PolicyTests(unittest.TestCase):
 def test_stack_and_A(self):
  rows=[row('1'),row('2','b')]; self.assertEqual(run(rows)['summary']['admitted'],2); self.assertEqual(rec(run(rows,'A'))['2']['reason'],'other_sleeve_same_coin_side')
 def test_budgets_and_release(self):
  rows=[row('1'),row('2','b'),row('3','c')]; x=rec(run(rows,'B')); self.assertEqual(x['2']['entry_risk'],8); self.assertEqual(x['3']['status'],'refused'); x=rec(run(rows,'B',multiple=2)); self.assertEqual(x['2']['entry_risk'],16); self.assertEqual(x['3']['status'],'refused')
  rows[2]['entry_utc']='2024-05-01T05:00:00Z'; rows[2]['exit_utc']='2024-05-01T06:00:00Z'; self.assertEqual(rec(run(rows,'B'))['3']['entry_risk'],16)
 def test_25_percent(self):
  rows=[row('1','acct4'),row('2','a'),row('3','b')]; x=rec(run(rows,'B')); self.assertEqual(x['2']['entry_risk'],16); self.assertEqual(x['3']['entry_risk'],8)
 def test_quarter_boundary(self):
  def case(loss):
   rows=[row('hold','a',exit='2024-05-03T05:00:00Z'),row('loss','b','ETH',exit='2024-05-01T01:00:00Z',R=loss),row('new','c',entry='2024-05-02T00:00:00Z',exit='2024-05-02T01:00:00Z')]
   return rec(simulate(rows,['a','b','c'],'B','equity'))['new']
  x=case(-50); self.assertEqual(x['status'],'admitted'); self.assertAlmostEqual(x['entry_risk']/x['normal_risk'],.25)
  self.assertEqual(case(-51)['reason'],'duplicate_budget_below_25pct')
 def test_slot_cap_and_aggregate(self):
  rows=[row('1','acct4','BTC'),row('2','acct4','ETH')]; self.assertEqual(rec(run(rows))['2']['reason'],'sleeve_position_cap')
  rows=[row(str(i),s,str(i)) for i,s in enumerate(['a','b','c','d','e','f','g'])]; self.assertEqual(rec(run(rows))['6']['reason'],'aggregate_entry_risk_cap')
 def test_own_coin_and_ties(self):
  rows=[row('1'),row('2','a',side=-1)]; self.assertEqual(rec(run(rows))['2']['reason'],'own_coin_slot')
  rows=[row('1'),row('2','b',entry='2024-05-01T05:00:00Z',exit='2024-05-01T06:00:00Z')]; self.assertEqual(rec(run(rows,'A'))['2']['status'],'admitted'); self.assertEqual(rec(run(rows,'A',tie='entry-first'))['2']['status'],'refused')
 def test_cairo_halt(self):
  rows=[row('1',R=-8,exit='2024-04-26T20:30:00Z',entry='2024-04-26T19:00:00Z'),row('2','b',entry='2024-04-26T20:45:00Z',exit='2024-04-26T22:00:00Z'),row('3','c',entry='2024-04-26T21:00:00Z',exit='2024-04-26T22:00:00Z')]; x=rec(run(rows)); self.assertEqual(x['2']['reason'],'cairo_daily_loss_halt'); self.assertEqual(x['3']['status'],'admitted')
 def test_funding_settlements(self):
  rows=[row('1',entry='2024-05-01T00:00:00Z',exit='2024-05-01T16:00:00Z'),row('2','b',entry='2024-05-01T00:00:01Z',exit='2024-05-01T16:00:01Z',side=-1)]
  for r in rows: r['initial_notional_per_risk']='100'
  x=rec(simulate(rows,['a','b'],'baseline','fixed',funding_bps=1)); self.assertEqual(x['1']['funding_settlements'],2); self.assertEqual(x['2']['funding_settlements'],2); self.assertAlmostEqual(x['1']['funding_debit'],.32); self.assertAlmostEqual(x['2']['funding_debit'],.32)
  with self.assertRaises(ValueError): simulate([row('3')],['a'],'baseline','fixed',funding_bps=1)
  self.assertEqual(rec(simulate([row('3')],['a'],'baseline','fixed'))['3']['funding_debit'],0)
 def test_explicit_sleeve_limits(self):
  rows=[row('1','EX04','BTC'),row('2','EX05','ETH'),row('3','EX04','SOL'),row('4','EX05','XRP')]
  for r in rows: r.update(risk_fraction=.002,max_positions=1)
  x=simulate(rows,['EX04','EX05'],'baseline','fixed'); z=rec(x)
  self.assertEqual(z['1']['entry_risk'],8); self.assertEqual(z['2']['entry_risk'],8)
  self.assertEqual(z['3']['reason'],'sleeve_position_cap'); self.assertEqual(z['4']['reason'],'sleeve_position_cap')
  self.assertEqual(x['summary']['nonjoined_other_count'],2); self.assertEqual(x['summary']['nonjoined_other_average_R'],1)
 def test_capital(self):
  with self.assertRaises(ValueError): run([],initial=0)
  x=rec(run([row('1',R=-300,exit='2024-05-01T01:00:00Z'),row('2','b',entry='2024-05-02T00:00:00Z',exit='2024-05-02T01:00:00Z')])); self.assertEqual(x['2']['reason'],'nonpositive_equity')
 def test_runs_isolated(self): self.assertEqual(run([row('1')])['summary']['terminal_equity'],run([row('1')])['summary']['terminal_equity'])
if __name__=='__main__': unittest.main()





