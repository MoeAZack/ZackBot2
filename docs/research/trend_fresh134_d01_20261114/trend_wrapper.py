"""Frozen local launcher: validate all input bytes then call the unmodified panel worker."""
import argparse, hashlib, json, pathlib, sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent
CACHE = pathlib.Path('C:/Dev/ZackBot_research_cache/longscout_fresh_20261114')
def sha(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def main():
    a=argparse.ArgumentParser(); a.add_argument('--orders', type=int, choices=[1,40], required=True); a.add_argument('--max-pos', type=int, choices=[1,3], required=True); a.add_argument('--out',required=True); a=a.parse_args()
    expected=json.loads((ROOT/'data_hashes.json').read_text())
    assert sha(CACHE/'input_manifest.json')=='fb0c0572b6c2bc5f569c64d89bd0ee2ca1dd9e3cf96b1ed8bd1110c341ed2f3e'
    assert len(expected)==135
    for s,h in expected.items(): assert sha(CACHE/'clean'/f'{s}_1h.csv')==h, s
    frozen=json.loads((ROOT/'frozen_hashes.json').read_text())
    for path,h in frozen.items(): assert sha(ROOT/path)==h, path
    coins=json.loads((ROOT/'selected.json').read_text())
    assert len(coins)==134 and set(expected)==set(coins)|{'BTCUSDT'}
    sys.path.insert(0,str(ROOT/'driver'))
    import sieve_v1_panel as panel
    assert panel.SV.orders(coins,40)[0]==sorted(coins)
    assert len({tuple(x) for x in panel.SV.orders(coins,40)})==40
    panel.worker(SimpleNamespace(worker=str(ROOT/'source'),case='exp_trend',max_pos=a.max_pos,universe=','.join(coins),data=str(CACHE/'clean'),out=a.out,orders=a.orders))
if __name__=='__main__': main()
