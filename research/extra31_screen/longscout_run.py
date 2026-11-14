"""LONGSCOUT-V1 runner: the frozen cases in longscout_v1_cases.json under Codex rule v1. RESEARCH ONLY.

Executes sieve_v1_panel.worker unchanged (same sizing, costs, eligibility, orders and gates); only the case table and the
source list are swapped in, and every order's closed trades are saved (sieve_v1_panel keeps order 0 only), so the
regime split can use all 40 orders. No network, no account, no runtime change.
  python research/extra31_screen/longscout_run.py <clean_csv_dir> <out_dir> --case NAME [--orders 40] [--universe S1,S2]
"""
import sys, os, json, subprocess, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_screen as RS, sieve_v1 as SV, sieve_v1_panel as SP

CASES_PATH = os.path.join(HERE, 'longscout_v1_cases.json')
CASES = json.load(open(CASES_PATH, encoding='utf-8'))
SP.CASES = CASES
RS.CONTRACT['sources'].update(CASES['sources'])
ORIG9 = ['ADAUSDT', 'AVAXUSDT', 'BNBUSDT', 'BTCUSDT', 'DOGEUSDT', 'ETHUSDT', 'LINKUSDT', 'SOLUSDT', 'XRPUSDT']


def worker(a):
    import pandas as pd
    cap = []
    orig = SV.order_stats

    def keep(tr, split=SV.SPLIT):
        cap.append(tr.copy())
        return orig(tr, split)
    SV.order_stats = keep
    SP.worker(a)
    rows = []
    for i, tr in enumerate(cap):
        if not len(tr): continue
        x = tr[[c for c in ('sym', 'side', 't_in', 't_out', 'pnl7', 'pnl15', 'pnl23', 'R', 'why') if c in tr]].copy()
        x['order'] = i // 2; x['scen'] = ('path', 'worst')[i % 2]
        rows.append(x)
    allt = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    allt.to_csv(os.path.join(a.out, f'{a.case}_all_orders_trades.csv.gz'), index=False, encoding='utf-8')
    rp = os.path.join(a.out, f'{a.case}_result.json')
    res = json.load(open(rp, encoding='utf-8'))
    res['longscout'] = dict(cases_sha256=RS.sha(CASES_PATH), runner_sha256=RS.sha(os.path.abspath(__file__)),
                            panel_runner_sha256=RS.sha(os.path.join(HERE, 'sieve_v1_panel.py')),
                            note='cases_sha256 / runner_sha256 above (from sieve_v1_panel) name that runner\'s files; this block names the LONGSCOUT ones')
    json.dump(res, open(rp, 'w', encoding='utf-8'), indent=1, default=str)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('data'); ap.add_argument('out'); ap.add_argument('--case', required=True, choices=sorted(CASES['cases']))
    ap.add_argument('--orders', type=int, default=40); ap.add_argument('--universe'); ap.add_argument('--worker')
    ap.add_argument('--max-pos', type=int, default=1, choices=[1])
    a = ap.parse_args()
    if a.worker: return worker(a)
    if a.case == 'exp_trend_regime_orig9' and a.universe is None: a.universe = ','.join(ORIG9)
    os.makedirs(a.out, exist_ok=True)
    coins = a.universe.split(',') if a.universe else SP.case_coins(a.case)
    msha, fsha, listed = RS.check_data(a.data, True, sorted(set([SV.BTC] + coins)))
    src = RS.export(CASES['cases'][a.case]['source'], a.out)
    cmd = [sys.executable, os.path.abspath(__file__), a.data, a.out, '--case', a.case, '--orders', str(a.orders), '--worker', src]
    if a.universe: cmd += ['--universe', a.universe]
    subprocess.run(cmd, check=True)
    json.dump(dict(data_manifest_sha256=msha, csv_sha256=fsha, csv_in_manifest=listed),
              open(os.path.join(a.out, f'{a.case}_inputs.json'), 'w', encoding='utf-8'), indent=1)


if __name__ == '__main__':
    main()
