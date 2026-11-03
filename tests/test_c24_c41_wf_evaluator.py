import os,sys
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import evaluate_c24_c41_wf as E
def test_familywise_threshold_is_full_ledger():assert E.FAMILY==44 and abs(E.ALPHA-.05/44)<1e-15
def test_chronological_risk_math():
 t=[{'R':1,'pnl':1,'symbol':'A','regime':'btc_above_ema200','t_out':'2025-04-01T00:00:00Z'},{'R':-2,'pnl':-2,'symbol':'B','regime':'btc_below_ema200','t_out':'2025-05-01T00:00:00Z'},{'R':3,'pnl':3,'symbol':'A','regime':'btc_above_ema200','t_out':'2025-06-01T00:00:00Z'}]
 m=E.metrics(t);assert m['r_sum']==2 and m['max_dd_r']==2 and m['pf']==2 and m['symbols']==2 and m['months']==3
def test_windows_end_before_holdout():assert all(e<='2026-09-28T00:00:00Z' for _,e in E.WINDOWS)
