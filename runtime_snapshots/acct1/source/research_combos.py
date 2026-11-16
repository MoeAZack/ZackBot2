import load_data, backtest as B, pandas as pd, itertools, json
bk=B.Book(load_data.load())
CORE=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','LINKUSDT','AVAXUSDT']
PY=dict(pyramid=dict(n=1,step_r=1.5,frac=.5))
SLV={
 'MOM40':dict(key='ema_mom',max_pos=8),
 'MOM40+PY':dict(key='ema_mom',max_pos=8,mgmt=PY),
 'ST8':dict(key='ema_st',max_pos=4,symbols=CORE),
 'ST8+PY':dict(key='ema_st',max_pos=4,symbols=CORE,mgmt=dict(stop_atr=2.5,**PY)),
 'DCA40':dict(key='dca_dip',max_pos=6),
 'BRK8':dict(key='breakout_pyramid',max_pos=4,symbols=CORE),
 'BEAR40':dict(key='bear_breakdown',max_pos=4),
 'ROT40':dict(key='rotation',max_pos=5),
 'SQZ40b':dict(key='squeeze_tp',max_pos=6,sides='both'),
}
rows=[]
for r in (1,2,3):
  for combo in itertools.combinations(SLV,r):
    if sum(('MOM40' in c) for c in combo)>1 or sum(('ST8' in c) for c in combo)>1: continue
    for risk in (0.01,0.02,0.03,0.05):
        sh=1/len(combo)
        tr,cv=B.run(bk,[dict(SLV[c],share=sh,risk=risk) for c in combo])
        s=B.stats(tr,cv); rows.append(dict(combo=' + '.join(combo),n=r,risk=risk,**s))
df=pd.DataFrame(rows); df.to_csv('results_combos.csv',index=False)
pd.set_option('display.width',250)
ok=df[(df.y1>0)&(df.y2>0)&(~df.busted)]
print('tested',len(df),'profitable both years',len(ok))
for risk in (0.01,0.02,0.03,0.05):
    print(f'\n== risk {risk:.0%}: best by sharpe ==')
    print(ok[ok.risk==risk].sort_values('sharpe',ascending=False).head(7)[['combo','end','ret','maxdd','sharpe','win','y1','y2','worst_month']].to_string(index=False))
