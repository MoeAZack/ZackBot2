from pathlib import Path
import json,hashlib,zipfile,numpy as np,pandas as pd
P=Path(__file__).parent;S=Path('F:/ZackBot2_data/MICROSTRUCTURE-FEASIBILITY-20261112');base=json.loads((S/'result.json').read_text());out={}
for meta in base['files']:
 s=meta['symbol'];path=S/(s+'-aggTrades-2026-09-01.zip');assert hashlib.sha256(path.read_bytes()).hexdigest()==meta['archive_sha256']
 chunks=[]
 with zipfile.ZipFile(path) as z:
  with z.open(z.namelist()[0]) as f:
   for c in pd.read_csv(f,usecols=[0,3,4,5],dtype='int64',chunksize=100000):chunks.append(c.to_numpy())
 a=np.concatenate(chunks);ids,first,last,t=a.T
 raw=first[1:]>last[:-1]+1;agg=ids[1:]!=ids[:-1]+1;rev=t[1:]<t[:-1];sil=t[1:]-t[:-1]>5000
 # Each bad adjacent pair contaminates the inclusive interval [previous timestamp,next timestamp].
 bad=raw|agg|rev|sil;lo=t[:-1][bad];hi=t[1:][bad]
 day=int(pd.Timestamp('2026-09-01').timestamp()*1000);rows=[]
 for k in range(2880):
  start=day+k*30000;end=start+30000;anchor=np.searchsorted(t,start,side='left')-1;finish=np.searchsorted(t,end,side='left')-1
  if anchor<0 or finish<=anchor:rows.append(False);continue
  left=t[anchor];right=t[finish]
  # Events, previous-price anchor and final price are all inside this span.
  touched=bool(np.any((lo<=right)&(hi>=left)))
  rows.append(not touched)
 out[s]={'rows':len(t),'raw_gap_pairs':int(raw.sum()),'aggregate_gap_pairs':int(agg.sum()),'timestamp_reversals':int(rev.sum()),'silences_gt5s':int(sil.sum()),'scheduled_bins':2880,'usable_bins':sum(rows),'usable_fraction':sum(rows)/2880,'usable_bin_indices':[i for i,v in enumerate(rows) if v],'passes_80pct':sum(rows)>=.8*2880}
report={'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'contract':'#219 6831735253 A; span previous-price anchor through final event, inclusive contamination','imports_claude_checker':False,'fields_used':'aggregate ID, first/last underlying trade ID, timestamp only','coins':out,'verdict':'DATA_INADEQUATE'}
(P/'independent_a_result.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
