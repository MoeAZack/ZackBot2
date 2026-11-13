import sys,json,hashlib
from pathlib import Path
import pandas as pd
sys.path.insert(0,'F:/ZackBot2_data/FWD-BOOKS-REVIEW-20261112/a1')
import run_books as RB
p=Path(__file__).parent; a=p.parent/'a1'; r=json.loads((p/'row.json').read_text()); receipts=json.loads((p/'receipts.json').read_text());m=json.loads((a/'freeze.json').read_text())
raw1,raw4,rules,files,violations=RB.load_inputs(m,'F:/ZackBot2_data/C1H-VENUE-RECHECK-20261112','F:/ZackBot2_data/FWD-01/data')
assert files==r['input_files_sha256'] and not violations
T=pd.Timestamp(r['T']); boundary=pd.Timestamp(r['boundary']); result={'row_sha256':hashlib.sha256((p/'row.json').read_bytes()).hexdigest(),'receipts_sha256':hashlib.sha256((p/'receipts.json').read_bytes()).hexdigest(),'matching_raw_input_hashes':len(files),'own_H5_predicates':{}}
for s,d in raw1.items():
 d=d[d.t<boundary].copy();c=d.c;tr=pd.concat([d.h-d.l,(d.h-c.shift()).abs(),(d.l-c.shift()).abs()],axis=1).max(axis=1); atr=tr.ewm(alpha=1/14,adjust=False).mean(); volmed=d.v.rolling(24,min_periods=24).median().shift(1)
 flags={'body_at_least_ATR':(c-d.o).abs()>=atr,'body_at_most_3ATR':(c-d.o).abs()<=3*atr,'volume_2x_prior24median':d.v>=2*volmed,'positive_span':d.h>d.l,'bullish':c>d.o,'close_top20pct':c>=d.l+0.8*(d.h-d.l),'EMA50_above_EMA200':c.ewm(span=50,adjust=False).mean()>c.ewm(span=200,adjust=False).mean()}
 z=pd.DataFrame(flags);z.insert(0,'t',d.t.astype(str)); z['signal']=pd.DataFrame(flags).all(axis=1);post=z[(d.t>=T)&(d.t+pd.Timedelta(hours=1)<=boundary)]
 assert len(post)==6 and not post.signal.any();result['own_H5_predicates'][s]=post.to_dict('records')

result['own_EMA_cross_checks']={}
for sym,d in raw4.items():
 d=d[d.t+pd.Timedelta(hours=4)<=boundary].copy(); e20=d.c.ewm(span=20,adjust=False).mean();e50=d.c.ewm(span=50,adjust=False).mean();e200=d.c.ewm(span=200,adjust=False).mean();cross=(e20>e50)&(e20.shift(1)<=e50.shift(1)); eligible=(d.t>=T.ceil('4h'))&(d.t+pd.Timedelta(hours=4)<=boundary)
 assert int(eligible.sum())==1
 z={'symbol':sym,'t':str(d.loc[eligible,'t'].iloc[0]),'ema20_cross_above_ema50':bool(cross[eligible].iloc[0]),'close_above_ema200':bool((d.c>e200)[eligible].iloc[0])}
 assert not (z['ema20_cross_above_ema50'] and z['close_above_ema200']),z
 result['own_EMA_cross_checks'][sym]=z

rerow,rereceipts=RB.FB.evaluate(raw1,raw4,rules,r['W0_1h'],r['W0_4h'],T,boundary)
expected={k:v for k,v in r.items() if k not in ('input_files_sha256','freeze_sha256')}
assert json.loads(json.dumps(rerow,default=str))==expected
assert json.loads(json.dumps(rereceipts,default=str))==receipts
assert len(receipts)==24 and all(not v for v in receipts.values())
assert len(r['books']['H5']['cases'])==12 and len(r['books']['EMA']['cases'])==12
for name,book in r['books'].items():
 for case in book['cases']:
  assert case['diagnostic']==(case['order']=='cohort')
  if name=='EMA':assert not case.get('baseline_flat',False) and book['scored_bars']==1
  if name in ('H5','EMA'):
   assert case['admitted']==case['closes']==case['nonparity_refusals']==case['guard_changed_entries']==0
   assert case['closed_cash']==case['terminal_marked_equity']==4000
   assert case['net_usd']==case['cash_growth_usd']+case['open_mark']==0
   assert case['target_funding_closed_usd']==case['target_funding_open_usd']==case['funding_usd']==case['fees_usd']==0
result.update(exact_semantic_row_match=True,exact_receipts_match=True,H5_cases=12,EMA_scored_cases=12,receipt_sets=24,verdict='PASS_ROW2_RESEARCH_ONLY')
(p/'independent_row_check.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ('own_H5_predicates','own_EMA_cross_checks')},indent=2))
