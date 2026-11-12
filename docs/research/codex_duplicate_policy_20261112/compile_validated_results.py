from pathlib import Path
import json,csv,hashlib
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
R=Path('F:/ZackBot2_data/DUP-COIN-SIDE-INDEPENDENT-20261112')
frames={n:pd.read_csv(R/f'results-{n}.csv') for n in ['validated-fast','validated-all12','validated-path-fast']}
claims={'A4_ALL12_2Y':[-92,-92,-92,-92],'A4_4H_2Y':[-9.5,-12.4,-9,-9.1],'A4_4H_LONG':[-26.5,-26.2,-26.1,-25.2],'NEW3_2Y':[-18.1,-14.2,-17.6,-17.0],'NEW3_LONG':[-3.3,-4,-2.6,-.7],'OLD3_2Y':[5.2,8.3,6.4,5.2],'OLD3_LONG':[13.4,48.1,11.2,13.4]}
rows=[]
for book,v in claims.items():
 for j,policy in enumerate(['baseline','refuse','cap1p5','cap2']):
  f=frames['validated-all12'] if book=='A4_ALL12_2Y' else frames['validated-fast'];a=f[(f.book==book)&(f.bps==15)&(f.scenario=='primary')&(f.policy_label==policy)].iloc[0]
  path=frames['validated-path-fast'];b=path[(path.book==book)&(path.bps==15)&(path.scenario=='primary')&(path.policy_label==policy)];c=path[(path.book==book)&(path.bps==15)&(path.scenario=='exit_open')&(path.policy_label==policy)]
  rows.append({'book':book,'policy':policy,'Claude_claim_return_pct':v[j],'independent_worst_exitclose_pct':a.return_pct,'independent_path_exitclose_pct':float(b.iloc[0].return_pct) if len(b) else None,'independent_path_exitopen_pct':float(c.iloc[0].return_pct) if len(c) else None,'difference_primary_pp':a.return_pct-v[j]})
pd.DataFrame(rows).to_csv(R/'claim_comparison.csv',index=False)
# Dollar contribution and policy feedback, not a claim that one subtraction equals a counterfactual replay.
base=R/'runs-validated/OLD3_LONG.15.primary.baseline/admitted.csv';ref=R/'runs-validated/OLD3_LONG.15.primary.refuse/admitted.csv';a=pd.read_csv(base);b=pd.read_csv(ref);target=b[(b.symbol=='XRPUSDT')&(b.sleeve=='ema_st_L')&b.entry_utc.str.startswith('2024-11-06T12:00:00')];assert len(target)==1
t=target.iloc[0];rb=frames['validated-fast'];get=lambda sc,pol:float(rb[(rb.book=='OLD3_LONG')&(rb.bps==15)&(rb.scenario==sc)&(rb.policy_label==pol)].iloc[0].return_pct)
attribution={'source':'worst/end-of-exitbar primary','outlier':t.to_dict(),'admitted_in_baseline':t.uniqueid in set(a.uniqueid),'baseline_return_pct':get('primary','baseline'),'refuse_return_pct':get('primary','refuse'),'spread_pp':get('primary','refuse')-get('primary','baseline'),'direct_outlier_pp':float(t.net_realized)/4000*100,'outlier_direct_share_of_spread':float(t.net_realized)/4000*100/(get('primary','refuse')-get('primary','baseline')),'drop_specific_trade_refuse_return_pct':get('drop_outlier','refuse'),'drop_all_2024_entries_baseline_return_pct':get('drop_2024','baseline'),'drop_all_2024_entries_refuse_return_pct':get('drop_2024','refuse')}
(R/'outlier_attribution.json').write_text(json.dumps(attribution,indent=2))
# By-year differences use closed realized equity; partial years retained explicitly.
y=pd.DataFrame(json.loads((R/'yearly-validated-fast.json').read_text()));p=y[(y.bps==15)&(y.scenario=='primary')].pivot_table(index=['book','year'],columns='policy',values='return');p['cap2_minus_base_pp']=100*(p['cap2']-p['baseline']);p['refuse_minus_base_pp']=100*(p['refuse']-p['baseline']);p.to_csv(R/'annual_differences_15bp.csv')
sc=pd.DataFrame(json.loads((R/'tapes/short15/generation_results.json').read_text())).pivot(index='strategy',columns='bps',values='mean_R');sc.to_csv(R/'scalp_raw_candidate_mean_R.csv')
# Save current live comparison from owner-authorized READ ONLY history; do not read keys/config.
import sys;sys.path.insert(0,'F:/ZackBot2_data');from account_gates import call
hist=[h for h in call(4,'/api/history')['history'] if not h.get('manual') and h.get('sleeve')!='MAN'];live=[]
for h in hist:live.append({k:h.get(k) for k in ['id','strategy_key','sleeve','symbol','side','closed','pnl','r','risk_usd','pnl_status','fees_actual']})
import math
measured=[h for h in live if h['pnl_status']=='measured' and h.get('pnl') is not None and h.get('r') is not None and math.isfinite(float(h['pnl'])) and math.isfinite(float(h['r']))];snapshot={'as_of_cairo':datetime.now(ZoneInfo('Africa/Cairo')).isoformat(),'closes':len(live),'measured_closes':len(measured),'measured_net_usd':sum(float(h['pnl']) for h in measured),'measured_sum_R':sum(float(h['r']) for h in measured),'rows':live,'manual_drills_excluded':True,'inference':'tiny live sample cannot override multiyear cost evidence'}
(R/'live_account4_readonly.json').write_text(json.dumps(snapshot,indent=2));print('OUTLIER',json.dumps({k:v for k,v in attribution.items() if k!='outlier'}));print('LIVE',json.dumps({k:v for k,v in snapshot.items() if k!='rows'}));print('B2 annual max abs PP',p.cap2_minus_base_pp.abs().max())
