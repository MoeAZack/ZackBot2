"""LONGSCOUT-FRESH-V1 runner: lc_volume_accum_1d, unchanged, on the fresh-coin set (rule v1). RESEARCH ONLY.
  python research/extra31_screen/longscout_fresh_run.py <fresh_dir> <out_dir> --manifest-sha SHA [--orders 40] [--pooled <extra31_clean_dir>]
<fresh_dir> holds input_manifest.json, selected.json and clean/. The manifest sha256 must equal the value committed in
git before the run. --pooled adds the 31 extra coins (copied CSVs from the frozen cache) as labelled secondary evidence.
"""
import sys, os, json, subprocess, argparse, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_screen as RS, sieve_v1 as SV, longscout_run as LR

FZ = os.path.join(HERE, 'longscout_fresh_v1.json')
CASE = 'lc_volume_accum_1d'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('fresh'); ap.add_argument('out'); ap.add_argument('--manifest-sha', required=True)
    ap.add_argument('--orders', type=int, default=40); ap.add_argument('--pooled')
    a = ap.parse_args()
    man = os.path.join(a.fresh, 'input_manifest.json')
    if RS.sha(man) != a.manifest_sha: raise SystemExit('fresh manifest sha256 differs from the committed value')
    m = json.load(open(man, encoding='utf-8'))
    if m['freeze_sha256'] != RS.sha(FZ): raise SystemExit('data was fetched under a different freeze file')
    sel = json.load(open(os.path.join(a.fresh, 'selected.json'), encoding='utf-8'))['selected']
    for s, v in m['symbols'].items():
        if RS.sha(os.path.join(a.fresh, 'clean', f'{s}_1h.csv')) != v['sha256']: raise SystemExit(f'{s}: csv sha differs from manifest')
    data = os.path.join(a.fresh, 'clean'); coins = sel; tag = 'fresh'
    if a.pooled:
        data = os.path.join(a.out, '_pooled_clean'); os.makedirs(data, exist_ok=True)
        for s in ['BTCUSDT'] + sel: shutil.copy(os.path.join(a.fresh, 'clean', f'{s}_1h.csv'), data)
        for s in RS.EXTRA31: shutil.copy(os.path.join(a.pooled, f'{s}_1h.csv'), data)
        coins = sorted(sel + RS.EXTRA31); tag = 'pooled'
    out = os.path.join(a.out, tag); os.makedirs(out, exist_ok=True)
    src = RS.export(LR.CASES['cases'][CASE]['source'], out)
    subprocess.run([sys.executable, os.path.join(HERE, 'longscout_run.py'), data, out, '--case', CASE, '--orders', str(a.orders),
                    '--worker', src, '--universe', ','.join(coins)], check=True)
    rp = os.path.join(out, f'{CASE}_result.json'); r = json.load(open(rp, encoding='utf-8'))
    r['fresh'] = dict(set=tag, status=('FRESH_COIN_PASS' if tag == 'fresh' and r['status'] == 'TRANSFER_SCREEN_PASS' else
                                       ('POOLED_SECONDARY_' + r['status'] if tag == 'pooled' else r['status'])),
                      n_coins=len(coins), freeze_sha256=RS.sha(FZ), manifest_sha256=a.manifest_sha)
    json.dump(r, open(rp, 'w', encoding='utf-8'), indent=1, default=str)
    print('FRESH', tag, r['fresh']['status'], r['medians'], flush=True)


if __name__ == '__main__':
    main()
