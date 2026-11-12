"""Exploratory long/short decomposition of first both-window-positive15bp grid cell."""
from pathlib import Path
import hashlib,importlib.util,copy
import strategies as S
p=Path('F:/ZackBot2_data/C1H-20261112/hourly_candidates.py');assert hashlib.sha256(p.read_bytes()).hexdigest()=='9b8516ef3a98bd83fb591ac751ae263a69c49f7bc7d5ea712741d4d3c4b038fc'
s=importlib.util.spec_from_file_location('direction_base',p);BASE=importlib.util.module_from_spec(s);s.loader.exec_module(BASE)
CANDIDATES={}
for side in ['long','short']:
 entry=copy.deepcopy(BASE.CANDIDATES['c1h_h5_volume_impulse']);entry['mgmt'].pop('prot',None);entry['mgmt'].update(stop_atr=3,tp_r=4,max_bars=48);entry['sides']=side;entry['name']='[RESEARCH DIRECTION CONTROL] volume impulse '+side;CANDIDATES['c1h_h5_s3_t4_b48_'+side]=entry
def set_panel(raw):BASE.set_panel(raw)
def register():S.STRATEGIES.update(CANDIDATES)
