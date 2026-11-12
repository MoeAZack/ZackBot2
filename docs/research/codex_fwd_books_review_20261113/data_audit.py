import json,hashlib,csv,datetime
from pathlib import Path
from decimal import Decimal as D
p=Path('F:/ZackBot2_data/FWD-BOOKS-REVIEW-20261112'); f=Path('F:/ZackBot2_data/FWD-01/data');c=Path('F:/ZackBot2_data/C1H-VENUE-RECHECK-20261112');m=json.loads((p/'freeze.json').read_text());man=json.loads((c/'input_manifest.json').read_text())['cohort9_1h'];h=3600000
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
parse=lambda t:int(datetime.datetime.fromisoformat(t).replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
def agg(x,t):
 z=[x.get(t+i*h) for i in range(4)]
 return None if None in z else (z[0][0],max(v[1] for v in z),min(v[2] for v in z),z[-1][3],sum(v[4] for v in z),sum(v[5] for v in z))
def eq(x,y):return x[:4]==y[:4] and all(abs(x[i]-y[i])<=D('1e-6')*max(abs(x[i]),D('1e-12')) for i in (4,5))
out={'source_head':'8f83494b','freeze_sha256':sha(p/'freeze.json'),'source_pins':{},'coins':{}}
for name,v in m['source_sha256'].items():
 q=p/'repro'/Path(name).name;out['source_pins'][name]={'sha256':sha(q),'matches':sha(q)==v}
for s in sorted(man):
 q=c/'inputs/cohort9_1h'/f'{s}.csv';canon={parse(r['t']):tuple(D(r[k]) for k in ('o','h','l','c','v','qv')) for r in csv.DictReader(q.open())}
 rows=[json.loads((f/f'{s}-{tf}.json').read_text()) for tf in ('1h','4h')]; maps=[{int(r[0]):tuple(D(str(r[k])) for k in (1,2,3,4,5,7)) for r in rr} for rr in rows];a,b=maps
 ca={t:agg(canon,t) for t in canon if t%(4*h)==0};ca={t:v for t,v in ca.items() if v is not None};over=ca.keys()&b.keys();cross=[t for t in b if agg(a,t) is not None];u={**b,**ca};end=min(max(a)+h,m['T_ms']);end4=end//(4*h)*(4*h)
 out['coins'][s]={'canonical_pin':sha(q)==man[s]['input_sha256'],'input_hashes':{tf:sha(f/f'{s}-{tf}.json') for tf in ('1h','4h')},'overlap4h':len(over),'overlap_mismatch':sum(not eq(ca[t],b[t]) for t in over),'fwd1h_4h_groups':len(cross),'fwd1h_4h_mismatch':sum(not eq(agg(a,t),b[t]) for t in cross),'coverage_end_ms':end,'gaps1h':sum(t not in a for t in range(parse(m['W0_1h_utc']),end,h)),'gaps4h':sum(t not in u for t in range(parse(m['W0_4h_utc']),end4,4*h)),'duplicates':[len(r)-len(mp) for r,mp in zip(rows,maps)],'ordered':[all(int(rr[i][0])<int(rr[i+1][0]) for i in range(len(rr)-1)) for rr in rows]}
print(json.dumps(out,indent=2));Path(__file__).with_suffix('.json').write_text(json.dumps(out,indent=2))
