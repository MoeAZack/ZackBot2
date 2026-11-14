"""BREADTH-FRESH134-D01: cp_y2_breadth_thrust_1d (646ab3fa), unchanged, on the fresh 134 coins + BTC context. RESEARCH ONLY.
  python research/extra31_screen/breadth_fresh134_run.py <fresh_dir> <out_dir> --max-pos 1|3 [--orders 40]
<fresh_dir> holds input_manifest.json, selected.json and clean/ (LONGSCOUT-FRESH-V1 data lock). Before any signal is computed:
the manifest sha256 must equal the value frozen in breadth_fresh134_v1.json, selected.json must equal the frozen 134-coin list,
and every CSV listed in the manifest (135 = BTC + 134) must match its sha256. The run itself is sieve_v1_panel.py's worker,
unchanged, with --universe = the 134 coins; this wrapper only checks inputs and adds the labelled fresh-set block and a
cost/diagnostic summary to the result. Output: <out_dir>/max<N>/.
"""
import sys, os, json, subprocess, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_screen as RS

FZ = os.path.join(HERE, 'breadth_fresh134_v1.json')
CASE = 'cp_y2_breadth_thrust_1d'
PANEL_RUNNER = os.path.join(HERE, 'sieve_v1_panel.py')


def load_freeze():
    return json.load(open(FZ, encoding='utf-8'))


def check_inputs(fresh, fz):
    """Raise SystemExit on any mismatch; return (coins, manifest sha, {sym: csv sha})."""
    man = os.path.join(fresh, 'input_manifest.json')
    msha = RS.sha(man)
    if msha != fz['data']['input_manifest_sha256']: raise SystemExit(f'manifest sha256 {msha} differs from the frozen value')
    m = json.load(open(man, encoding='utf-8'))
    sel = sorted(json.load(open(os.path.join(fresh, 'selected.json'), encoding='utf-8'))['selected'])
    coins = list(fz['universe']['traded'])
    if sel != coins: raise SystemExit('selected.json differs from the frozen 134-coin list')
    want = set(coins) | {RS.BTC}
    if set(m['symbols']) != want: raise SystemExit(f'manifest symbols differ from BTC + frozen list: {sorted(set(m["symbols"]) ^ want)}')
    got = {}
    for s, v in sorted(m['symbols'].items()):
        h = RS.sha(os.path.join(fresh, 'clean', f'{s}_1h.csv'))
        if h != v['sha256']: raise SystemExit(f'{s}: csv sha256 differs from the manifest')
        got[s] = h
    return coins, msha, got


def summarize(out, tag):
    """Cost decomposition and diagnostics from order 0's path trades (closed trades only)."""
    import pandas as pd
    p = os.path.join(out, f'{tag}_order0_path_trades.csv')
    tr = pd.read_csv(p)
    if not len(tr): return dict(n=0)
    R = 8.0
    return dict(n=int(len(tr)), sumR7=float(tr.pnl7.sum() / R), sumR15=float(tr.pnl15.sum() / R), sumR23=float(tr.pnl23.sum() / R),
                extra_cost_15bp_R=float((tr.pnl7 - tr.pnl15).sum() / R), extra_cost_23bp_R=float((tr.pnl7 - tr.pnl23).sum() / R),
                turnover_usd=float(tr.turnover.sum()), distinct_entry_dates=int(pd.to_datetime(tr.t_in).dt.floor('D').nunique()),
                note='pnl7 already carries the engine fee+slippage (7 bp) and the funding proxy; 15/23 bp add 8/16 bp of turnover')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('fresh'); ap.add_argument('out'); ap.add_argument('--max-pos', type=int, required=True, choices=[1, 3])
    ap.add_argument('--orders', type=int, default=40)
    a = ap.parse_args()
    fz = load_freeze()
    coins, msha, csv_sha = check_inputs(a.fresh, fz)
    out = os.path.join(a.out, f'max{a.max_pos}'); os.makedirs(out, exist_ok=True)
    src = RS.export(fz['candidate']['source'], out)
    subprocess.run([sys.executable, PANEL_RUNNER, os.path.join(a.fresh, 'clean'), out, '--case', CASE, '--orders', str(a.orders),
                    '--worker', src, '--universe', ','.join(coins), '--max-pos', str(a.max_pos)], check=True)
    tag = CASE if a.max_pos == 1 else f'{CASE}_mp{a.max_pos}'
    rp = os.path.join(out, f'{tag}_result.json'); r = json.load(open(rp, encoding='utf-8'))
    run = fz['runs']['primary' if a.max_pos == 1 else 'secondary']
    passed = all(r['gates'].values())
    r['universe'] = 'fresh134'
    r['label'] = fz['label']
    r['fresh134'] = dict(role='primary' if a.max_pos == 1 else 'secondary', status=run['pass_status'] if passed else 'FAIL',
                         orders=a.orders, freeze_sha256=RS.sha(FZ), wrapper_sha256=RS.sha(os.path.abspath(__file__)),
                         input_manifest_sha256=msha, n_csv_checked=len(csv_sha), csv_sha256=csv_sha,
                         order0_cost_summary=summarize(out, tag),
                         note=run.get('note', 'rule v1 primary'))
    json.dump(r, open(rp, 'w', encoding='utf-8'), indent=1, default=str)
    print('FRESH134', tag, r['fresh134']['status'], r['medians'], flush=True)


if __name__ == '__main__':
    main()
