"""Matched fixed-stop exit control; original frozen signal functions unchanged."""
import importlib.util,copy
from pathlib import Path
import strategies as S
spec=importlib.util.spec_from_file_location('c1h_frozen_base',Path('F:/ZackBot2_data/C1H-20261112/hourly_candidates.py'))
BASE=importlib.util.module_from_spec(spec);spec.loader.exec_module(BASE)
CANDIDATES=copy.deepcopy(BASE.CANDIDATES)
for entry in CANDIDATES.values():
 entry['mgmt'].pop('prot',None)
 entry['name']='[RESEARCH EXIT CONTROL] '+entry['name']
def set_panel(raw):BASE.set_panel(raw)
def register():S.STRATEGIES.update(CANDIDATES)
