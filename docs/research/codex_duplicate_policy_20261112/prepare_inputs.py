import json,hashlib
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
ROOT=Path('F:/ZackBot2_data/DUP-COIN-SIDE-INDEPENDENT-20261112');out=ROOT/'inputs';out.mkdir(exist_ok=True)
S=['BTCUSDT','ETHUSDT','XRPUSDT','SOLUSDT','DOGEUSDT','BNBUSDT','ADAUSDT','LINKUSDT','AVAXUSDT'];SRC15=Path('C:/Dev/paper-backup-20261105/candles_15m_research');SRC4=Path('C:/Dev/ZackBot2_newstrat/data_long/4h');receipts=[];sets={'short15':{},'short4':{},'long4':{}}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
for sym in S:
 p=SRC15/(sym+'_15m.csv');d=pd.read_csv(p);d['t']=pd.to_datetime(d['t'],unit='ms',utc=True).dt.tz_localize(None);d=d[(d.t>=pd.Timestamp('2024-10-11'))&(d.t<pd.Timestamp('2026-09-28'))].sort_values('t').reset_index(drop=True)
 assert not d.t.duplicated().any() and 'qv' in d and d.qv.notna().all()
 sets['short15'][sym]=d
 h=d.set_index('t').resample('4h',closed='left',label='left').agg({'o':'first','h':'max','l':'min','c':'last','v':'sum','qv':'sum'});n=d.set_index('t').c.resample('4h').count();h=h[n==16].reset_index();sets['short4'][sym]=h
 receipts.append({'symbol':sym,'tf':'15m','path':str(p),'sha256':sha(p),'first':str(d.t.iloc[0]),'last':str(d.t.iloc[-1]),'rows':len(d),'native_qv':True})
 if sym!='ADAUSDT':
  p=SRC4/(sym+'_4h.csv');d=pd.read_csv(p,parse_dates=['t']);d=d[d.t<pd.Timestamp('2026-09-28')].sort_values('t').reset_index(drop=True);sets['long4'][sym]=d;receipts.append({'symbol':sym,'tf':'long4h','path':str(p),'sha256':sha(p),'first':str(d.t.iloc[0]),'last':str(d.t.iloc[-1]),'rows':len(d),'qv':'qv' in d})
coverage={}
for name,data in sets.items():
 ts=set.intersection(*(set(d.t) for d in data.values()));ts=sorted(ts);step=pd.Timedelta(minutes=15) if name=='short15' else pd.Timedelta(hours=4)
 missing=int((ts[-1]-ts[0])/step)+1-len(ts);coverage[name]={'symbols':list(data),'rows':len(ts),'first':str(ts[0]),'last':str(ts[-1]),'missing_common_grid':missing}
 assert missing==0,'Common data grid has gaps: '+name
 folder=out/name;folder.mkdir(exist_ok=True)
 for sym,d in data.items():d[d.t.isin(ts)].to_csv(folder/(sym+'.csv'),index=False)
manifest={'head':'7b5beecb3a964d4ce36aa7187a734093ffe6818a','created_cairo':datetime.now(ZoneInfo('Africa/Cairo')).isoformat(),'source_sha256':{p.name:sha(p) for p in (ROOT/'source').glob('*.py')},'receipts':receipts,'coverage':coverage,'window':'consumedDEV only; end2026-09-28UTC exclusive; no holdout tuning','qv':'native15m quote volume summed only complete16-candle4h bars; long4h lacksqv soexp_vwaprev excluded, never zero-filled','candidate_generation':{'start_equity':1e9,'risk_fraction':1e-6,'max_pos':'all symbols','daily_halt':1.,'max_lev':10,'fees_per_side':[0,.0007,.0015,.0023],'slip':0.,'funding':0.,'exchange_rules':'legacy floors only; per-lot R tape, not a filter qualification','intrabar':'worst','warmup':220,'stop_guard':'requirecompletecurve; rejectgeneratorcapitalearlytermination'},'overlay_primary':'fixed8or16 risk; cap2.5%cash equity; Cairorealizeddailyhalt3%; 10%closedDDreportedNOTenforced; exit assumedendofexitbar; savedslorder','sensitivities':['exitbaropentimestamps vsprimaryclose','percentageequityratherthanfixedrisk','both-side3bp/8hentry-notionalfundingstressbookedatclose'],'default':'RESEARCH_ONLY_DEFAULT_OFF_NO_ACCOUNT_CHANGES'}
with (ROOT/'preregistration.json').open('x') as f:json.dump(manifest,f,indent=2)
print(json.dumps(coverage,indent=2))
