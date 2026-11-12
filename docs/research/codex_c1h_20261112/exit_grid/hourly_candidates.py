"""Frozen exploratory exit grid on six existing entries; all are dependent variant trials."""
import importlib.util,copy,itertools,hashlib
from pathlib import Path
import strategies as S
base=Path('F:/ZackBot2_data/C1H-20261112/hourly_candidates.py')
assert hashlib.sha256(base.read_bytes()).hexdigest()=='9b8516ef3a98bd83fb591ac751ae263a69c49f7bc7d5ea712741d4d3c4b038fc'
spec=importlib.util.spec_from_file_location('c1h_grid_base',base);BASE=importlib.util.module_from_spec(spec);spec.loader.exec_module(BASE)
CANDIDATES={}
for stop,target,bars in itertools.product([2,3,4],[2,3,4],[12,24,48]):
 for key,entry in BASE.CANDIDATES.items():
  item=copy.deepcopy(entry);item['mgmt'].pop('prot',None);item['mgmt'].update(stop_atr=stop,tp_r=target,max_bars=bars,be_r=0,trail_atr=0)
  ident=f'{key}__s{stop}_t{target}_b{bars}';item['name']='[RESEARCH EXIT GRID] '+ident;CANDIDATES[ident]=item
def set_panel(raw):BASE.set_panel(raw)
def register():S.STRATEGIES.update(CANDIDATES)
