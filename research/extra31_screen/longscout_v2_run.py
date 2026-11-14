"""LONGSCOUT-V2 runner: frozen cases in longscout_v2_cases.json on the 165-coin set (fresh134 + extra31). RESEARCH ONLY.
Rule-v1 execution is longscout_run.worker -> sieve_v1_panel.worker, unchanged; this file swaps in the v2 case table,
builds the combined data folder after checking both pinned manifests, and adds the frozen fresh/extra subset gate.
  python research/extra31_screen/longscout_v2_run.py <fresh_dir> <extra_clean_dir> <out_dir> --case NAME [--orders 40]
"""
import sys, os, json, subprocess, argparse, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_screen as RS, sieve_v1 as SV, sieve_v1_panel as SP, longscout_run as LR

CASES_PATH = os.path.join(HERE, 'longscout_v2_cases.json')
CASES = json.load(open(CASES_PATH, encoding='utf-8'))
SP.CASES = CASES; LR.CASES = CASES
RS.CONTRACT['sources'].update(CASES['sources'])
FRESH_MANIFEST = 'fb0c0572b6c2bc5f569c64d89bd0ee2ca1dd9e3cf96b1ed8bd1110c341ed2f3e'


def subset_gate(out, case, fresh):
    import numpy as np, pandas as pd
    t = pd.read_csv(os.path.join(out, f'{case}_all_orders_trades.csv.gz'))
    t = t[t.scen == 'path']
    res = {}
    for name, keep in (('fresh134', t.sym.isin(fresh)), ('extra31', t.sym.isin(RS.EXTRA31))):
        per = t[keep].groupby('order').pnl15.mean() / SV.RISK_USD
        per = per.reindex(range(int(t.order.max()) + 1))
        res[name] = dict(median_meanR15=None if per.isna().any() else float(per.median()),
                         median_n=float(t[keep].groupby('order').size().reindex(per.index, fill_value=0).median()))
    res['pass'] = all(res[k]['median_meanR15'] is not None and res[k]['median_meanR15'] > 0 for k in ('fresh134', 'extra31'))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('fresh'); ap.add_argument('extra'); ap.add_argument('out')
    ap.add_argument('--case'); ap.add_argument('--orders', type=int, default=40); ap.add_argument('--worker'); ap.add_argument('--universe')
    ap.add_argument('--max-pos', type=int, default=1, choices=[1])
    a = ap.parse_args()
    if a.worker:                                   # worker mode: argv = data out (fresh slot = data dir, extra slot = out dir)
        a.data, a.out = a.fresh, a.extra
        return LR.worker(a)
    if RS.sha(os.path.join(a.fresh, 'input_manifest.json')) != FRESH_MANIFEST: raise SystemExit('fresh manifest differs')
    RS.check_data(a.extra, True, RS.EXTRA31)       # strict: the frozen 40-coin cache manifest
    m = json.load(open(os.path.join(a.fresh, 'input_manifest.json'), encoding='utf-8'))
    fresh = json.load(open(os.path.join(a.fresh, 'selected.json'), encoding='utf-8'))['selected']
    data = os.path.join(a.out, '_data'); os.makedirs(data, exist_ok=True)
    for s in ['BTCUSDT'] + fresh:
        p = os.path.join(a.fresh, 'clean', f'{s}_1h.csv')
        if RS.sha(p) != m['symbols'][s]['sha256']: raise SystemExit(f'{s}: sha differs from manifest')
        if not os.path.exists(os.path.join(data, f'{s}_1h.csv')): shutil.copy(p, data)
    for s in RS.EXTRA31:
        if not os.path.exists(os.path.join(data, f'{s}_1h.csv')): shutil.copy(os.path.join(a.extra, f'{s}_1h.csv'), data)
    coins = sorted(fresh + RS.EXTRA31)
    out = os.path.join(a.out, a.case); os.makedirs(out, exist_ok=True)
    src = RS.export(CASES['cases'][a.case]['source'], out)
    subprocess.run([sys.executable, os.path.abspath(__file__), data, out, '--case', a.case, '--orders', str(a.orders),
                    '--worker', src, '--universe', ','.join(coins)], check=True)
    rp = os.path.join(out, f'{a.case}_result.json'); r = json.load(open(rp, encoding='utf-8'))
    sg = subset_gate(out, a.case, fresh)
    r['wide165'] = dict(subset_gate=sg, status='WIDE165_PASS' if (r['status'] == 'TRANSFER_SCREEN_PASS' and sg['pass']) else 'FAIL',
                        cases_sha256=RS.sha(CASES_PATH), runner_sha256=RS.sha(os.path.abspath(__file__)), n_coins=len(coins))
    json.dump(r, open(rp, 'w', encoding='utf-8'), indent=1, default=str)
    print('V2', a.case, r['wide165']['status'], r['gates'], {k: (round(v, 4) if v is not None else None) for k, v in r['medians'].items()}, sg, flush=True)


if __name__ == '__main__':
    main()
