import load_data, backtest as B, strategies as S, pandas as pd, itertools, json
bk=B.Book(load_data.load())
CORE=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','LINKUSDT','AVAXUSDT']
U={'core8':CORE,'top40':bk.syms}
rows=[]
for key,st in S.STRATEGIES.items():
    side_opts=[st['sides']] + (['both'] if st['sides']=='long' and key not in ('dca_dip','hot_coin') else [])
    for uni,syms in U.items():
        for sides in side_opts:
            for risk in (0.01,0.03):
                mp=4 if uni=='core8' else 6
                tr,cv=B.run(bk,[dict(key=key,share=1.0,risk=risk,max_pos=mp,symbols=syms,sides=sides)])
                rows.append(dict(key=key,name=st['name'],style=st['style'],universe=uni,sides=sides,risk=risk,**B.stats(tr,cv)))
df=pd.DataFrame(rows); df.to_csv('results_single.csv',index=False)
pd.set_option('display.width',250); pd.set_option('display.max_rows',200)
print(df[df.risk==0.01].sort_values('sharpe',ascending=False)[['name','universe','sides','end','ret','maxdd','sharpe','trades','win','pf','y1','y2','worst_month']].to_string(index=False))
