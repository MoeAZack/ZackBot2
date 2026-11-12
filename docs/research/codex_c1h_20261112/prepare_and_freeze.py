import json,hashlib,shutil
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
R=Path('F:/ZackBot2_data/C1H-20261112'); h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
SYMS=['BTCUSDT','ETHUSDT','XRPUSDT','SOLUSDT','DOGEUSDT','BNBUSDT','ADAUSDT','LINKUSDT','AVAXUSDT']
manifest={}
for dataset in ['cohort9_1h','long8_1h']:
 out=R/'inputs'/dataset;out.mkdir(parents=True,exist_ok=True); rows={}
 for sym in SYMS:
  if dataset=='long8_1h' and sym=='ADAUSDT':continue
  if dataset=='cohort9_1h':
   src=Path('F:/ZackBot2_data/DUP-COIN-SIDE-INDEPENDENT-20261112/inputs/short15')/(sym+'.csv')
   d=pd.read_csv(src,parse_dates=['t']).set_index('t')
   ag={'o':'first','h':'max','l':'min','c':'last','v':'sum'}
   if 'qv' in d.columns:ag['qv']='sum'
   counts=d.c.resample('1h',closed='left',label='left').count()
   z=d.resample('1h',closed='left',label='left').agg(ag).loc[counts==4].reset_index()
   assert z.t.is_unique and z.t.diff().iloc[1:].eq(pd.Timedelta(hours=1)).all()
   dst=out/(sym+'.csv');z.to_csv(dst,index=False)
  else:
   src=Path('C:/Dev/ZackBot2_newstrat/data_long/1h')/(sym+'_1h.csv');dst=out/(sym+'.csv');shutil.copyfile(src,dst);z=pd.read_csv(dst,parse_dates=['t'])
   assert z.t.is_unique and z.t.diff().iloc[1:].eq(pd.Timedelta(hours=1)).all()
  rows[sym]={'source':str(src),'source_sha256':h(src),'input_sha256':h(dst),'rows':len(z),'first_candle_utc':z.t.iloc[0].isoformat(),'last_candle_utc':z.t.iloc[-1].isoformat()}
 assert len({(v['rows'],v['first_candle_utc'],v['last_candle_utc']) for v in rows.values()})==1
 manifest[dataset]=rows
rules_src=Path('C:/Dev/ZackBot2_newstrat/data/exchange_rules_testnet.json');shutil.copyfile(rules_src,R/'exchange_rules.json')
(R/'input_manifest.json').write_text(json.dumps(manifest,indent=2))
rules=[
 ('H1','EMA trend pullback resumption','EMA50>EMA200 and EMA200>lag24; prior close below EMA20 and prior low<=EMA20; current close above EMA20 and prior high; mirror short',2,3,24),
 ('H2','Compressed range breakout','prior12 high-low<=4 priorATR; close first crosses prior12 extreme; volume>=1.25 prior24 median; EMA50/200 aligned; mirror',2,3,24),
 ('H3','Flat-market Keltner reclaim','ADX<20; abs(EMA50-lag24)/ATR<1; close reclaims EMA50-2ATR after previous close below its band; mirror upper',2,2,12),
 ('H4','RSI2 trend reset','EMA50/200 aligned and close beyond EMA200; previous3 RSI2 min<10 and current cross above30; mirror max>90 cross below70',2,2.5,18),
 ('H5','Volume impulse continuation','body 1..3ATR; volume>=2 prior24 median; bullish close upper20% with EMA50>200, mirror short',2.5,3,24),
 ('H6','BTC-relative momentum','alt24hreturn-BTC24hreturn>.01; EMA50>200, close>EMA50, first close through prior6 high, volume>=1.25 prior24 median; mirror; BTCexcluded',2,3,24)]
prereg={'id':'C1H-20261112','frozen_cairo':datetime.now(ZoneInfo('Africa/Cairo')).isoformat(),'source_head':'7b5beecb3a964d4ce36aa7187a734093ffe6818a','source_sha256':{p.name:h(p) for p in (R/'source').glob('*.py')},'data_manifest_sha256':h(R/'input_manifest.json'),'exchange_rules_sha256':h(R/'exchange_rules.json'),'rules':[dict(id=a,name=b,rule=c,stop_atr=d,tp_r=e,max_bars=f) for a,b,c,d,e,f in rules],'protection_common':{'be_r':0,'trail_atr':0,'prot':{'at_r':1,'trail_atr':2}},'candidate_module_sha256':h(R/'hourly_candidates.py'),'costs_per_executed_side_bps':[7,15,23],'scenarios':['path','worst'],'gross_diagnostic':{'fee_slip_funding':0,'path':'path'},'funding_proxy':{'both_sides_charged_per_hour':.0001/8,'native_funding':False},'capital':4000,'share_per_sleeve':1/12,'risk_fraction_of_share':.024,'initial_risk_usd':8,'max_positions_per_sleeve':1,'daily_halt':.03,'max_leverage':10,'risk_model_limit':'unchanged BT.run compounds equity-based risk, initial$8 not fixed live$8; candidate activation requires actual account-fit check','no_holdout_claim':'ALL historical data consumed DEV; 2025 split and chronological halfyears are diagnostic stability checks, not fresh sealed holdout','trial_accounting':'six additional frozen configurations in previously researched families, not six independently novel families; all outcomes retained; no parameter changes based on results','priority_order':'cohort9 worst15 all six first, then cost/path ladders; long8 secondary','economics_screen':{'min_closed_total':100,'min_closed_each_pre_post_2025':30,'mean_R_15_positive_each_split':True,'mean_R_23_positive_overall':True,'worst15_positive_overall':True,'exclude_best_coin_mean_R15_positive':True,'positive_complete_halfyear_fraction15':'>=0.5','minimum_closed_per_calendar_week':2,'drawdown':'report R/%/duration and label; no blanket DD rejection','frequency_basket_target':'at least5 closes/week after shared-account caps; solo frequency is not additive proof'},'activation':'No auto qualification from historical screen; independent signal/execution/cost parity, native-or-explicitstress funding, account4 fixedrisk/minimum sizing, shared-basket interaction, protective stop/rollback testnet gates; no account1-3 action','runtime_actions':'NONE; isolated research only'}
(R/'preregistration.json').write_text(json.dumps(prereg,indent=2))
print(json.dumps({'frozen_cairo':prereg['frozen_cairo'],'datasets':{k:{'symbols':len(v),'bars':next(iter(v.values()))['rows']} for k,v in manifest.items()},'prereg_sha256':h(R/'preregistration.json'),'source_head':prereg['source_head']}))