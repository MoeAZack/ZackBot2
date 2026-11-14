"""R6-CAPACITY3-D01 (Codex GO #219 6874975399): ONE preregistered dependent capacity diagnostic. RESEARCH ONLY.

Label DEPENDENT_SECONDARY_CAPACITY_VARIANT: not a new strategy, never a substitute for the max1 R6-TNET163-FIT-D01 result.
Identical to r6_fit163_confirm.py (r6_regime_flip_4h at acc8d0ee, frozen venue163 list, frozen current testnet filters,
same D01 inputs/clocks/warmup/split, $8 risk at 10m equity, $3,333.33 cap, 40 orders path/worst, 7/15/23 bp, funding,
3ATR stop / state-leave / 120 bars, rule-v1 + fresh132/extra31 subset gates) except max concurrent positions 1 -> 3.
Native admission: backtest.run's max_lev cap is account-wide notional (shared by open positions from the first bar);
no independent max1 tapes are overlaid. A max_pos=1 control is run through this same harness and must equal the
published max1 fit per order (it is a harness check, not a second variant). No max5, grid, side/coin rescue or rule change.
Extra reporting (no new gate): trades/week vs control, refusals (exchange-filter skips, cap-shrunk entries), marked
equity DD (closed equity + open P&L at bar close, base costs) vs closed R15 DD, terminal open positions and marks,
concentration (ex-top-3/5 coins), halves, ex-best, cost levels.
  LONGSCOUT_CASES=longscout_v6_cases.json python research/extra31_screen/r6_cap3_confirm.py <fresh_dir> <extra_clean_dir> <out_dir>
"""
import sys, os, json, subprocess, shutil, types
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('LONGSCOUT_CASES', 'longscout_v6_cases.json')
import run_screen as RS, sieve_v1 as SV, sieve_v1_panel as SP, longscout_v2_run as V2
import r6_fit163_confirm as FC

CASE = FC.CASE
MAX1_FIT_RESULT = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'docs', 'research', 'r6_fit163_native_confirm_20261114',
                               'r6_regime_flip_4h_result.json')
MAX1_FIT_RESULT_SHA = '7422025888048f385e9de43884fd627d9d1d16b9d487aa55a09021d78ebe877f'   # PR #349 head 60c0a1c5
LABEL = 'DEPENDENT_SECONDARY_CAPACITY_VARIANT'


def worker(src, data, out, mp, coins):
    """Runs inside one process per max_pos value. Captures trades, equity curves and cap-shrunk sizing around the
    unchanged sieve_v1_panel.worker / backtest.run."""
    import numpy as np, pandas as pd
    sys.path[:0] = [src, os.path.join(src, 'research')]
    import backtest as BT, feasibility as F
    SP.VARIANT = dict(max_pos=[3], cases=[CASE], label=LABEL)          # admits ONLY mp3 for this ONE case
    trades, curves, shrunk = [], [], []
    orig_stats, orig_run, orig_size = SV.order_stats, BT.run, F.size_check

    def keep_stats(tr, split=SV.SPLIT):
        trades.append(tr.copy()); return orig_stats(tr, split)

    def keep_run(*a, **k):
        shrunk.append(0)
        tr, cv = orig_run(*a, **k); curves.append(cv); return tr, cv

    def size_check(qty_raw, qty, px, rule):
        if qty < qty_raw * (1 - 1e-9): shrunk[-1] += 1                  # aggregate cap reduced the $8 size
        return orig_size(qty_raw, qty, px, rule)
    SV.order_stats, BT.run, F.size_check = keep_stats, keep_run, size_check
    ns = types.SimpleNamespace(case=CASE, max_pos=mp, universe=','.join(coins), data=data, out=out, orders=40, worker=src)
    SP.worker(ns)
    tag = CASE if mp == 1 else f'{CASE}_mp{mp}'
    rows, diag = [], []
    T0 = pd.Timestamp(SV.T0)
    for j, (tr, cv) in enumerate(zip(trades, curves)):
        order, scen = j // 2, ('path', 'worst')[j % 2]
        if len(tr):
            x = tr[[c for c in ('sym', 'side', 't_in', 't_out', 'pnl7', 'pnl15', 'pnl23', 'R', 'why', 'qty') if c in tr]].copy()
            x['order'] = order; x['scen'] = scen; rows.append(x)
        closed = float(tr.pnl.sum()) if len(tr) else 0.0
        closed15 = (tr.pnl15.sum() if len(tr) else 0.0)
        r15 = (tr.sort_values('t_out').pnl15 / SV.RISK_USD).cumsum() if len(tr) else pd.Series([0.0])
        mdd_marked = float((cv.cummax() - cv).max() / SV.RISK_USD) if len(cv) else 0.0
        weeks = (cv.index[-1] - T0).days / 7 if len(cv) else float('nan')
        by = tr.groupby('sym').pnl15.sum().sort_values(ascending=False) if len(tr) else pd.Series(dtype=float)
        ex = {n: (float(tr[~tr.sym.isin(by.index[:n])].pnl15.mean() / SV.RISK_USD) if len(tr) > 0 and (~tr.sym.isin(by.index[:n])).any() else None) for n in (3, 5)}
        feas = cv.attrs.get('feasibility') or {}
        diag.append(dict(order=order, scen=scen, n=int(len(tr)), trades_per_week=len(tr) / weeks if weeks else None,
                         closed_sumR15=float(closed15 / SV.RISK_USD), maxdd_closed_R15=float((r15.cummax() - r15).max()),
                         maxdd_marked_R_base=mdd_marked,
                         terminal_open_mark_R_base=float((cv.iloc[-1] - SV.START - closed) / SV.RISK_USD) if len(cv) else 0.0,
                         ex_top3_meanR15=ex[3], ex_top5_meanR15=ex[5],
                         attempts=feas.get('attempts'), executed=feas.get('executed'), filter_skipped=sum((feas.get('skipped') or {}).values()),
                         rule_blocked=feas.get('rule_blocked'), cap_shrunk_entries=shrunk[j]))
    pd.concat(rows, ignore_index=True).to_csv(os.path.join(out, f'{tag}_all_orders_trades.csv.gz'), index=False, encoding='utf-8')
    json.dump(diag, open(os.path.join(out, f'{tag}_diag.json'), 'w', encoding='utf-8'), indent=1)


def subset_gate(out, tag, fresh):
    import pandas as pd
    t = pd.read_csv(os.path.join(out, f'{tag}_all_orders_trades.csv.gz')); t = t[t.scen == 'path']
    res = {}
    for name, keep in (('fresh132', t.sym.isin(fresh)), ('extra31', t.sym.isin(RS.EXTRA31))):
        per = t[keep].groupby('order').pnl15.mean().reindex(range(40)) / SV.RISK_USD
        res[name] = dict(median_meanR15=None if per.isna().any() else float(per.median()))
    res['pass'] = all(res[k]['median_meanR15'] is not None and res[k]['median_meanR15'] > 0 for k in ('fresh132', 'extra31'))
    return res


def main():
    if sys.argv[1] == '--worker':
        src, data, out, mp, coins = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6].split(',')
        return worker(src, data, out, mp, coins)
    fresh_dir, extra_dir, out_root = sys.argv[1:4]
    if os.path.basename(V2.CASES_PATH) != 'longscout_v6_cases.json': raise SystemExit('LONGSCOUT_CASES must be longscout_v6_cases.json')
    case = V2.CASES['cases'][CASE]
    if case['commit'] != FC.SOURCE: raise SystemExit('case source differs from acc8d0ee')
    fz_p, ru_p = os.path.join(FC.FIT, 'freeze.json'), os.path.join(FC.FIT, 'rules.json')
    if RS.sha(fz_p) != FC.FREEZE_SHA or RS.sha(ru_p) != FC.RULES_SHA: raise SystemExit('fit freeze/rules sha differs')
    if RS.sha(MAX1_FIT_RESULT) != MAX1_FIT_RESULT_SHA: raise SystemExit('max1 fit result differs from PR #349')
    fz = json.load(open(fz_p, encoding='utf-8')); rules = json.load(open(ru_p, encoding='utf-8')); coins = fz['symbols']
    if len(coins) != 163 or coins != sorted(set(coins)) or [s for s in coins if s not in rules['symbols']]: raise SystemExit('bad list/filters')
    if RS.sha(os.path.join(fresh_dir, 'input_manifest.json')) != V2.FRESH_MANIFEST: raise SystemExit('fresh manifest differs')
    RS.check_data(extra_dir, True, RS.EXTRA31)
    m = json.load(open(os.path.join(fresh_dir, 'input_manifest.json'), encoding='utf-8'))
    fresh134 = json.load(open(os.path.join(fresh_dir, 'selected.json'), encoding='utf-8'))['selected']
    if sorted(set(fresh134 + RS.EXTRA31) - set(coins)) != fz['excluded_unavailable']: raise SystemExit('163 != 165 minus exclusions')
    fresh = sorted(set(fresh134) & set(coins))
    data = os.path.join(out_root, '_data'); os.makedirs(data, exist_ok=True)
    for s in ['BTCUSDT'] + fresh:
        p = os.path.join(fresh_dir, 'clean', f'{s}_1h.csv')
        if RS.sha(p) != m['symbols'][s]['sha256']: raise SystemExit(f'{s}: sha differs from manifest')
        shutil.copy(p, data)
    for s in RS.EXTRA31: shutil.copy(os.path.join(extra_dir, f'{s}_1h.csv'), data)
    max1 = json.load(open(MAX1_FIT_RESULT, encoding='utf-8'))
    summary = dict(trial='R6-CAPACITY3-D01', label=LABEL, parent=fz['id'], wrapper_sha256=RS.sha(os.path.abspath(__file__)),
                   freeze_sha256=FC.FREEZE_SHA, rules_sha256=FC.RULES_SHA, runs={})
    procs = {}
    for mp in (1, 3):
        out = os.path.join(out_root, f'mp{mp}'); os.makedirs(out, exist_ok=True)
        src = RS.export(case['source'], out)
        shutil.copyfile(ru_p, os.path.join(src, 'data', 'exchange_rules_testnet.json'))
        if RS.sha(os.path.join(src, 'data', 'exchange_rules_testnet.json')) != FC.RULES_SHA: raise SystemExit('rules swap failed')
        procs[mp] = (out, subprocess.Popen([sys.executable, os.path.abspath(__file__), '--worker', src, data, out, str(mp), ','.join(coins)],
                                           stdout=open(os.path.join(out_root, f'mp{mp}.log'), 'w', encoding='utf-8'), stderr=subprocess.STDOUT))
    for mp, (out, p) in procs.items():
        if p.wait() != 0: raise SystemExit(f'mp{mp} worker failed')
    import numpy as np
    for mp, (out, _) in procs.items():
        tag = CASE if mp == 1 else f'{CASE}_mp{mp}'
        r = json.load(open(os.path.join(out, f'{tag}_result.json'), encoding='utf-8'))
        if r['coins'] != coins or r['max_pos'] != mp: raise SystemExit('worker ran a different list / max_pos')
        sg = subset_gate(out, tag, fresh)
        diag = json.load(open(os.path.join(out, f'{tag}_diag.json'), encoding='utf-8'))
        dp = [d for d in diag if d['scen'] == 'path']
        med = lambda k: float(np.median([d[k] for d in dp if d[k] is not None]))
        rule_ok = all(r['gates'].values())
        summary['runs'][f'mp{mp}'] = dict(
            status=(('FIT_CONTROL_PASS' if mp == 1 else 'CAPACITY3_PASS') if rule_ok and sg['pass'] else ('FIT_CONTROL_FAIL' if mp == 1 else 'CAPACITY3_FAIL')),
            gates=r['gates'], subset_gate=sg, medians=r['medians'], worst_path=r['worst_path_diagnostic'],
            meanR15_p10=r['meanR15_p10'], meanR15_min=r['meanR15_min'], meanR15_max=r['meanR15_max'], order0=r['order0'],
            diag_median=dict((k, med(k)) for k in ('trades_per_week', 'closed_sumR15', 'maxdd_closed_R15', 'maxdd_marked_R_base',
                                                     'terminal_open_mark_R_base', 'ex_top3_meanR15', 'ex_top5_meanR15', 'filter_skipped', 'cap_shrunk_entries')),
            diag_max=dict((k, float(max(d[k] for d in dp))) for k in ('maxdd_closed_R15', 'maxdd_marked_R_base')),
            ex_top3_orders_positive=sum(1 for d in dp if (d['ex_top3_meanR15'] or 0) > 0))
        if mp == 1:   # harness check: must equal the published max1 fit per order
            keys = ['n', 'meanR15', 'meanR23', 'meanR15_early', 'meanR15_late', 'meanR15_ex_best']
            diff = max(abs(r['per_order'][s][k][q] - max1['per_order'][s][k][q]) for s in ('path', 'worst') for k in range(40) for q in keys)
            summary['runs']['mp1']['max_abs_diff_vs_pr349'] = diff
            if diff > 1e-12: raise SystemExit(f'control differs from the published max1 fit: {diff}')
    json.dump(summary, open(os.path.join(out_root, 'capacity3_summary.json'), 'w', encoding='utf-8'), indent=1, default=str)
    print(json.dumps({k: dict(status=v['status'], medians=v['medians'], diag=v['diag_median']) for k, v in summary['runs'].items()}, indent=1, default=str))


if __name__ == '__main__':
    main()
