"""Independent signal/cash arithmetic review. Never imports the submitted runners or strategy code."""
import hashlib
import io
import json
import math
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd

REPO=Path(r'F:\ZackBot2_data\C1H-20261112\review-branch')
OUT=Path(r'F:\ZackBot2_data\SIEVE-REVIEW-20261113')
DATA=Path(r'C:\Dev\ZackBot_research_cache\h5_wide_d01_20261112')
HEAD='0ca354406a618a51751497a2aa8cb5cb2ea3405e'
MASTER='9cb4341f2d94c21434492515b8e85f2b42c148ce'
OUT.mkdir(exist_ok=True)
def blob(p,head=HEAD):
    return subprocess.check_output(['git','show',head+':'+p],cwd=REPO)
def digest(b): return hashlib.sha256(b).hexdigest()
def artifact(p):
    b=blob(p); (OUT/Path(p).name).write_bytes(b); return b
def readcsv(p): return pd.read_csv(io.BytesIO(artifact(p)))
original={'BTCUSDT','ETHUSDT','XRPUSDT','SOLUSDT','DOGEUSDT','BNBUSDT','ADAUSDT','LINKUSDT','AVAXUSDT'}
manifest_bytes=(DATA/'input_manifest.json').read_bytes()
assert digest(manifest_bytes)=='99af9bfb4a9b29ede80f8f3ff54541912d74d03b3b93d984432832c934bdfa95'
manifest=json.loads(manifest_bytes); coins=sorted(set(manifest['symbols'])-original)
assert len(coins)==31
rules=json.loads(blob('data/exchange_rules_testnet.json',MASTER))['symbols']

expected={
'screen/summary.csv':'2f58c9d955554194a34365994554bf6a68eb69598a8d61be4575799d895baba9',
'sieve_v1_panel/exp_trend_order0_path_trades.csv':'e4891c88211cb9ba0293a8d7a5a399df23347f4002961f9e5b6e250ae9975973',
'sieve_v1_panel/exp_trend_signals.csv':'8c7aa3a15d01a7afde8eff410195032f920e84b268b5c63370a83fe14f19e78e',
'sieve_v1_panel/bear_breakdown_order0_path_trades.csv':'7686961b7173be86f1a46e73608f30a122cbd9c3a5dfc079f14158e636ab1bad',
'sieve_v1_panel/bear_breakdown_signals.csv':'0ff98594716cc67b0b638dc5d9b4cefea9e1cd655e5d21249ab30a844c1c8bd2',
'sieve_v1/ema_st_order0_path_trades.csv':'d8576b723c7c209f0313e46f6ce9086519a63045a48d5fe433f5efcbc45f3012',
'sieve_v1/c101_order0_path_trades.csv':'167ffbce2a0fb4d5' # prefix provided in the request
}
hashes={}
for rel,sha in expected.items():
    b=artifact('docs/research/extra31_screen/'+rel)
    lf=b.replace(b'\r\n',b'\n'); crlf=lf.replace(b'\n',b'\r\n')
    hashes[rel]={'LF':digest(lf),'CRLF':digest(crlf),'requested':sha,'match':digest(crlf).startswith(sha)}
    assert hashes[rel]['match'], rel

hourly={}; four={}; eligibility={}; input_hashes={}
for sym in ['BTCUSDT']+coins:
    p=DATA/'clean'/(sym+'_1h.csv'); b=p.read_bytes(); input_hashes[sym]=digest(b)
    assert digest(b)==manifest['symbols'][sym]['csv_sha256']
    d=pd.read_csv(io.BytesIO(b)); d['t']=pd.to_datetime(d.t,unit='ms') if np.issubdtype(d.t.dtype,np.number) else pd.to_datetime(d.t)
    assert d.t.is_monotonic_increasing and not d.t.duplicated().any()
    assert d.t.diff().iloc[1:].eq(pd.Timedelta(hours=1)).all()
    hourly[sym]=d
    # Prior complete UTC days only. No outcome-dependent trimming or gap repair.
    q=d.set_index('t').qv.resample('1D').agg(['sum','count'])
    daily=q['sum'].where(q['count']==24)
    prior=daily.rolling(30,min_periods=30).median().shift(1)
    liq=(prior.reindex(pd.DatetimeIndex(d.t.dt.floor('D'))).to_numpy()>=1e7) & (np.arange(len(d))+1>=720)
    groups=d.set_index('t').resample('4h',label='left',closed='left')
    f=groups.agg(dict(o='first',h='max',l='min',c='last',v='sum',qv='sum'))
    f=f[groups.size()==4].dropna(subset=['o','h','l','c']).reset_index()
    c=f.c
    for n in [20,50,200]: f['e'+str(n)]=c.ewm(span=n,adjust=False).mean()
    tr=np.maximum(f.h-f.l,np.maximum((f.h-c.shift()).abs().fillna(0),(f.l-c.shift()).abs().fillna(0)))
    f['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    f['ret42']=c/c.shift(42)-1; f['ret180']=c/c.shift(180)-1
    f['ll20']=f.l.rolling(20).min().shift(1); f['hh10']=f.h.rolling(10).max().shift(1)
    four[sym]=f
    eligibility[sym]=(pd.Series(liq,index=pd.DatetimeIndex(d.t)).reindex(pd.DatetimeIndex(f.t)+pd.Timedelta(hours=3)).fillna(False).to_numpy(dtype=bool)
                      & (np.arange(len(f))>=220) & (f.t>=pd.Timestamp('2022-08-26 06:00')).to_numpy())
    print('independent input',sym,len(f),flush=True)

btc=four['BTCUSDT'].set_index('t'); btc_down=btc.c<btc.e200
signals={}; comparisons={}
for case in ['exp_trend','bear_breakdown']:
    rows=[]; signals[case]={}
    for sym in coins:
        f=four[sym]; c=f.c; ml=(f.ret42>0)&(f.ret180>0); ms=(f.ret42<0)&(f.ret180<0)
        if case=='exp_trend':
            le=(f.e20>f.e50)&(f.e20.shift()<=f.e50.shift())&(c>f.e200)&ml
            se=(f.e50>f.e20)&(f.e50.shift()<=f.e20.shift())&(c<f.e200)&ms
            lx=(f.e20<f.e50)|~ml; sx=(f.e20>f.e50)|~ms
        else:
            down=btc_down.reindex(pd.DatetimeIndex(f.t)).fillna(False).to_numpy(dtype=bool)
            le=pd.Series(False,index=f.index); se=down&(c<f.ll20)&(f.e20<f.e50)
            lx=le.copy(); sx=(c>f.hh10)|~down
        signals[case][sym]={k:np.asarray(v,dtype=bool) for k,v in dict(le=le,se=se,lx=lx,sx=sx).items()}
        for side,arr in [('long',le),('short',se)]:
            for j in np.flatnonzero(arr): rows.append((sym,str(f.t.iloc[j]),side,bool(eligibility[sym][j])))
    own=pd.DataFrame(rows,columns=['sym','decision_bar_open_utc','side','eligible'])
    own.to_csv(OUT/(case+'_independent_signals.csv'),index=False)
    reference=readcsv('docs/research/extra31_screen/sieve_v1_panel/'+case+'_signals.csv')
    def tuples(df):
        return set((str(r.sym),pd.Timestamp(r.decision_bar_open_utc).isoformat(),str(r.side),bool(r.eligible)) for r in df.itertuples())
    a,b=tuples(own),tuples(reference)
    comparisons[case]={'raw_own':len(own),'raw_reference':len(reference),'matched':len(a&b),'missing':len(b-a),'extra':len(a-b),
                       'eligible_own':int(own.eligible.sum()),'eligible_reference':int(reference.eligible.sum()),
                       'missing_examples':sorted(b-a)[:10],'extra_examples':sorted(a-b)[:10]}
    assert a==b and len(own)==len(reference), case+' signal discrepancy'
    print(case,'signals',comparisons[case],flush=True)

def qty_floor(q,step):
    if not step: return q
    dec=max(0,-int(math.floor(math.log10(step)))) if step<1 else 0
    return round(math.floor(q/step+1e-9)*step,dec)

def lot_exit(case,sym,j,sd,entry,worst=False):
    """Two simple frozen management rules, independently walked on real 4h candles."""
    f=four[sym]; atr0=f.atr.iloc[j-1]; dist=(2.5 if case=='exp_trend' else 2.0)*atr0
    stop=entry-sd*dist; target=entry+sd*3*dist; best=entry
    funding_unit=0.
    for k in range(j,len(f)):
        r=f.iloc[k]; o,h,l,c=r.o,r.h,r.l,r.c; atr=f.atr.iloc[k-1]
        funding_unit += c*0.00005
        if sd*(o-stop)<=0: return k,o,'stop',funding_unit
        stop_inside=sd*((l if sd==1 else h)-stop)<=0
        if worst or c==o: pts=[o,h,l,c] if sd==1 else [o,l,h,c]
        else: pts=[o,l,h,c] if c>o else [o,h,l,c]
        for leg,price in enumerate(pts):
            previous=pts[max(0,leg-1)]; move=sd*(price-previous)
            if leg>0 and move<0 and sd*(price-stop)<=0:
                exit_ref=stop if sd*(previous-stop)>=0 else previous
                return k,exit_ref,'stop',funding_unit
            if case=='exp_trend' and not stop_inside and (leg==0 or move>0) and sd*(price-target)>=0:
                return k,target,'tp',funding_unit
            best=max(best,price) if sd==1 else min(best,price)
            if case=='bear_breakdown' and leg>0:
                level=best-sd*3*atr
                stop=max(stop,level) if sd==1 else min(stop,level)
        ex=signals[case][sym]['lx' if sd==1 else 'sx'][k]
        if ex or (case=='exp_trend' and k-j>=180): return k,c,'signal' if ex else 'time',funding_unit
    return None

cash={}; cash_rows=[]
for case in ['exp_trend','bear_breakdown']:
    ref=readcsv('docs/research/extra31_screen/sieve_v1_panel/'+case+'_order0_path_trades.csv')
    eq=1e7; errors=[]; q_fixed_diff=0; records=[]
    for r in ref.itertuples():
        f=four[r.sym]; j=int(np.flatnonzero(f.t.eq(pd.Timestamp(r.t_in)))[0]); sd=int(r.side)
        er=f.o.iloc[j]; ep=er*(1+sd*.0002); stopdist=(2.5 if case=='exp_trend' else 2)*f.atr.iloc[j-1]
        rule=rules.get(r.sym,dict(step=None,min_qty=0,min_notional=5))
        q=qty_floor(min((eq*8/1e7)/stopdist,(eq*3333.33/1e7)/ep),rule.get('step'))
        q_fixed=qty_floor(min(8/stopdist,3333.33/ep),rule.get('step'))
        q_fixed_diff+=q!=q_fixed
        assert q>=rule.get('min_qty',0) and q*ep>=rule.get('min_notional',0)
        end,xref,reason,fund_unit=lot_exit(case,r.sym,j,sd,ep)
        xp=xref*(1-sd*.0002); gross=sd*(xp-ep)*q
        fee=(ep+xp)*q*.0005; fund=fund_unit*q
        p7=gross-fee-fund; turnover=(er+xref)*q; p15=p7-turnover*.0008; p23=p7-turnover*.0016
        eq+=p7
        outtime=f.t.iloc[end]
        sig_ok=bool(signals[case][r.sym]['le' if sd==1 else 'se'][j-1] and eligibility[r.sym][j-1])
        errs={z:abs(float(v)-float(getattr(r,z))) for z,v in dict(pnl7=p7,pnl15=p15,pnl23=p23,turnover=turnover).items()}
        record=dict(case=case,sym=r.sym,t_in=str(r.t_in),t_out_own=str(outtime),qty=q,qty_exact_fixed=q_fixed,
                    entry_reference=er,entry_fill=ep,exit_reference=xref,exit_fill=xp,gross=gross,fees7=fee,funding=fund,
                    pnl7=p7,pnl15=p15,pnl23=p23,turnover=turnover,why_own=reason,why_reference=r.why,
                    entry_signal_eligible=sig_ok,exit_matches=(pd.Timestamp(r.t_out)==outtime and reason==r.why),**{k+'_error':v for k,v in errs.items()})
        records.append(record); errors.append(errs); cash_rows.append(record)
    cash[case]={'closed_lots_checked':len(ref),'entry_signals_all_eligible':all(r['entry_signal_eligible'] for r in records),
                'all_exit_times_and_reasons_match':all(r['exit_matches'] for r in records),
                'max_errors':{k:max(e[k] for e in errors) for k in errors[0]},'quantity_different_from_exact_fixed_sizing':q_fixed_diff,
                'conditional_exact_fixed_qty_net15_usd':sum(r['pnl15']*r['qty_exact_fixed']/r['qty'] for r in records),
                'net15_usd':sum(r['pnl15'] for r in records),'scope':'All submitted order-0 closed lots independently sized and managed conditional on their admission; symbol admission and all 40 orders not independently rerun.'}
    assert cash[case]['entry_signals_all_eligible'] and cash[case]['all_exit_times_and_reasons_match']
    assert max(cash[case]['max_errors'].values())<1e-8
    print(case,'cash',cash[case],flush=True)
pd.DataFrame(cash_rows).to_csv(OUT/'independent_closed_lots.csv',index=False)

# Independently choose order-0 admissions as well, rather than condition every check on submitted lots.
# Each frozen rule has one lot, no adds/partials, and no effective account-wide gates in the submitted setup.
timeline=pd.DatetimeIndex(four['BTCUSDT'].t)
offset={s:int(timeline.get_indexer([four[s].t.iloc[0]])[0]) for s in coins}
admission={}
for case in ['exp_trend','bear_breakdown']:
    for worst in [False,True]:
        i=221; equity=1e7; selected=[]; terminal=[]
        while i<len(timeline):
            candidate=None
            for sym in coins:
                j=i-1-offset[sym]
                if j<0 or j>=len(four[sym]) or not eligibility[sym][j]: continue
                le=signals[case][sym]['le'][j]; se=signals[case][sym]['se'][j]
                if not le and not se: continue
                entry_j=j+1
                if entry_j>=len(four[sym]): continue
                sd=1 if le else -1; f=four[sym]
                ref_entry=f.o.iloc[entry_j]; entry=ref_entry*(1+sd*.0002)
                dist=(2.5 if case=='exp_trend' else 2)*f.atr.iloc[j]
                rule=rules.get(sym,dict(step=None,min_qty=0,min_notional=5))
                qty=qty_floor(min(equity*8/1e7/dist,equity*3333.33/1e7/entry),rule.get('step'))
                if qty<rule.get('min_qty',0) or qty*entry<rule.get('min_notional',0) or qty>rule.get('max_qty',float('inf')): continue
                candidate=(sym,entry_j,sd,ref_entry,entry,qty); break
            if candidate is None:
                i+=1; continue
            sym,j,sd,ref_entry,entry,qty=candidate; f=four[sym]
            end=lot_exit(case,sym,j,sd,entry,worst)
            if end is None:
                funding=float(f.c.iloc[j:].sum())*.00005*qty
                mark=float(f.c.iloc[-1]); entry_fee=entry*qty*.0005
                mtm=sd*(mark-entry)*qty-entry_fee-funding
                exit_fill=mark*(1-sd*.0002)
                liq=sd*(exit_fill-entry)*qty-entry_fee-exit_fill*qty*.0005-funding
                turnover=(ref_entry+mark)*qty
                terminal.append(dict(sym=sym,side=sd,t_in=str(f.t.iloc[j]),qty=qty,mark=mark,
                                     unrealized_mark7_including_entry_fee_and_funding=mtm,
                                     liquidation_pnl15=liq-turnover*.0008,liquidation_pnl23=liq-turnover*.0016))
                break
            k,exit_ref,why,fund_unit=end
            xp=exit_ref*(1-sd*.0002); gross=sd*(xp-entry)*qty; fees=(entry+xp)*qty*.0005
            p7=gross-fees-fund_unit*qty; turnover=(ref_entry+exit_ref)*qty
            selected.append(dict(sym=sym,side=sd,t_in=str(f.t.iloc[j]),t_out=str(f.t.iloc[k]),why=why,
                                 qty=qty,pnl7=p7,pnl15=p7-turnover*.0008,pnl23=p7-turnover*.0016))
            equity+=p7; i=offset[sym]+k+1
        mode='worst' if worst else 'path'
        reference=readcsv('docs/research/extra31_screen/sieve_v1_panel/'+case+'_order0_'+mode+'_trades.csv')
        expected_rows=[(r.sym,int(r.side),str(r.t_in),str(r.t_out),str(r.why)) for r in reference.itertuples()]
        actual_rows=[tuple(r[k] for k in ['sym','side','t_in','t_out','why']) for r in selected]
        match=actual_rows==expected_rows
        error={col:max(abs(a[col]-b) for a,b in zip(selected,reference[col])) if match and selected else None for col in ['pnl7','pnl15','pnl23']}
        assert match and all(e<1e-8 for e in error.values()), case+' '+mode+' admission discrepancy'
        admission[case+'_'+mode]={'closed_lots':len(selected),'ordered_entry_exit_set_matches':match,'max_pnl_errors':error,
                                 'terminal_open_lots':terminal,'unknown_exchange_rule_symbols':sorted(set(coins)-set(rules)),
                                 'closed_net15_usd':sum(r['pnl15'] for r in selected)}
        pd.DataFrame(selected).to_csv(OUT/(case+'_own_order0_'+mode+'_admissions.csv'),index=False)
        print(case,mode,'independent admission',admission[case+'_'+mode],flush=True)

summary=readcsv('docs/research/extra31_screen/screen/summary.csv')
print('screen columns',list(summary.columns),flush=True)
result={'head':HEAD,'source':MASTER,'method':'Independent source-reading implementation; no submitted research/strategy/backtest code imported.',
        'hashes':hashes,'manifest_sha256':digest(manifest_bytes),'inputs_sha256':input_hashes,'signals':comparisons,'cash':cash,'independent_order0_admission':admission}
(OUT/'independent_review.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
print('DONE',str(OUT/'independent_review.json'),flush=True)
