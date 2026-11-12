import hashlib, json, sys
from pathlib import Path
import pandas as pd

BASE = Path('F:/ZackBot2_data/C1H-VENUE-RECHECK-20261112')
OUT = BASE/'independent_signals'
OUT.mkdir(exist_ok=False)
manifest = json.loads((BASE/'input_manifest.json').read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
checks = []
for dataset, entries in manifest.items():
    for symbol, meta in entries.items():
        p = BASE/'inputs'/dataset/(symbol+'.csv')
        checks.append(dict(dataset=dataset,symbol=symbol,sha256=sha(p),expected=meta['input_sha256']))
pd.DataFrame(checks).to_csv(OUT/'input_hashes.csv',index=False)
assert all(x['sha256']==x['expected'] for x in checks), 'Input hash mismatch'

# Entire independent calculation is completed and saved before reference imports.
for dataset, entries in manifest.items():
    rows=[]
    for symbol in entries:
        d=pd.read_csv(BASE/'inputs'/dataset/(symbol+'.csv'))
        prev=d.c.shift(1)
        tr=pd.concat([d.h-d.l,(d.h-prev).abs(),(d.l-prev).abs()],axis=1).max(axis=1)
        atr=tr.ewm(alpha=1/14,adjust=False).mean()
        fast=d.c.ewm(span=50,adjust=False).mean(); slow=d.c.ewm(span=200,adjust=False).mean()
        body=(d.c-d.o).abs(); span=d.h-d.l
        eligible=(body>=atr)&(body<=3*atr)&(d.v>=2*d.v.shift(1).rolling(24,min_periods=24).median())&(span>0)
        for side, condition in [('long',(d.c>d.o)&(d.c>=d.l+.8*span)&(fast>slow)),('short',(d.c<d.o)&(d.c<=d.l+.2*span)&(fast<slow))]:
            idx=d.index[eligible & condition & (d.index>=219) & (d.index<len(d)-1)]
            rows.extend((symbol,pd.Timestamp(d.t.iloc[i],tz='UTC').isoformat(),side) for i in idx)
    pd.DataFrame(rows,columns=['symbol','decision_candle_utc','side']).sort_values(['symbol','decision_candle_utc','side']).to_csv(OUT/(dataset+'_own.csv'),index=False)

# Separate comparator: import frozen implementation only after own outputs exist.
sys.path.insert(0,str(BASE/'source')); sys.path.insert(0,str(BASE))
import strategies
import hourly_candidates
summary={'input_hashes_ok':len(checks),'source_sha256':{'hourly_candidates.py':sha(BASE/'hourly_candidates.py'),'strategies.py':sha(BASE/'source'/'strategies.py')},'datasets':{}}
for dataset, entries in manifest.items():
    rows=[]
    for symbol in entries:
        d=pd.read_csv(BASE/'inputs'/dataset/(symbol+'.csv'))
        s=hourly_candidates.BASE.h5(strategies.indicators(d),{}, {'symbol':symbol})
        for side,key in [('long','le'),('short','se')]:
            for i in d.index[s[key] & (d.index>=219) & (d.index<len(d)-1)]:
                rows.append((symbol,pd.Timestamp(d.t.iloc[i],tz='UTC').isoformat(),side))
    cols=['symbol','decision_candle_utc','side']
    ref=pd.DataFrame(rows,columns=cols)
    own=pd.read_csv(OUT/(dataset+'_own.csv'))
    m=own.merge(ref,on=cols,how='outer',indicator=True)
    for name,flag in [('matched','both'),('missing','right_only'),('extra','left_only')]:
        m.loc[m._merge==flag,cols].to_csv(OUT/(dataset+'_'+name+'.csv'),index=False)
    summary['datasets'][dataset]={'own':len(own),'own_by_side':own.side.value_counts().to_dict(),'reference':len(ref),'matched':int((m._merge=='both').sum()),'missing_from_own':int((m._merge=='right_only').sum()),'extra_in_own':int((m._merge=='left_only').sum())}
    ref.to_csv(OUT/(dataset+'_reference.csv'),index=False)
summary['ledger_files_present']=[str(p) for p in Path(r'F:\ZackBot2_data\C1H-VENUE-RECHECK-20261112\results').rglob('*') if p.is_file()]
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))