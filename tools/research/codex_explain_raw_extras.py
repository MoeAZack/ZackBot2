import csv,json
from pathlib import Path
from datetime import datetime,timezone
root=Path('F:/ZackBot2_data/N6-independent-a3095360');ledger=Path('C:/Dev/ZackBot2_newstrat/docs/research/newstrat_cp/cp_n6_btc_lead_catchup_4h.path.trades.csv')
def dt(s):return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
with ledger.open(newline='') as f:held=[{'symbol':r['sym'],'start':dt(r['t_in']),'end':dt(r['t_out'])} for r in csv.DictReader(f)]
counts={'same_coin_interior':0,'capacity_full_interior':0,'entry_or_exit_boundary':0,'not_explained_by_ledger_occupancy':0};unexplained=[]
with (root/'PRIMARY_FIRST_INSIDE_RANGE.extra.csv').open(newline='') as f:
 for r in csv.DictReader(f):
  t=dt(r['candle_open_utc']);active=[x for x in held if x['start']<t<x['end']]
  if any(x['symbol']==r['symbol'] for x in active):counts['same_coin_interior']+=1
  elif len(active)>=2:counts['capacity_full_interior']+=1
  elif any(x['start']==t or x['end']==t for x in held):counts['entry_or_exit_boundary']+=1
  else:counts['not_explained_by_ledger_occupancy']+=1;unexplained.append(r)
result={'diagnostic_only':'CONDITIONED on reference entry/exit intervals; NOT independent portfolio/execution replay. Strict interiors avoid inferring path order on entry/exit candles.','extra_total':sum(counts.values()),'counts':counts,'unexplained_examples':unexplained[:10]};(root/'extra_occupancy_diagnostic.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
