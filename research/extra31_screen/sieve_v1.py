"""SIEVE-V1: one frozen case under Codex's catalog-and-sieve rule v1 (one position at a time, 40 symbol orders). RESEARCH ONLY.

The frozen cases, implementation choices and gates are in sieve_v1_cases.json; this file only executes them.
  python research/extra31_screen/sieve_v1.py <clean_csv_dir> <out_dir> --case ema_st|c101 [--orders 40] [--universe S1,S2]
--universe (other coins) exists for the smoke test only. No network, no account, no runtime change.
"""
import sys, os, json, subprocess, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_screen as RS

CASES = json.load(open(os.path.join(HERE, 'sieve_v1_cases.json'), encoding='utf-8'))
START, RISK_USD, CAP_USD = 10000000.0, 8.0, 3333.33
T0, SPLIT, SEED = '2022-08-26 06:00', '2025-01-01 00:00', 20261112
BTC = RS.BTC


def case_coins(case):
    c = CASES['cases'][case]
    return sorted(s for s in RS.EXTRA31 if s not in c.get('prior_used', []))


def orders(coins, n):
    import numpy as np
    base = sorted(coins)
    return [base] + [[base[i] for i in np.random.default_rng(SEED + k).permutation(len(base))] for k in range(1, n)]


def liquidity_1h(d):
    """D01 point-in-time rule on a coin's 1h frame (t, qv): prior 30 COMPLETE UTC days' median daily quote volume >= $10m
    and >= 720 consecutive hourly bars. Returns a bool Series indexed by t."""
    import pandas as pd
    day = d.t.dt.floor('D')
    dq = d.groupby(day).qv.sum(); dn = d.groupby(day).size()
    dq = dq.where(dn == 24)
    dq = dq.reindex(pd.date_range(dq.index.min(), dq.index.max(), freq='D'))
    med = dq.rolling(30, min_periods=30).median().shift(1)
    liq = med.reindex(day).values >= 1e7
    run = d.groupby(d.t.diff().ne(pd.Timedelta(hours=1)).cumsum()).cumcount().values + 1
    return pd.Series(liq & (run >= 720), index=pd.DatetimeIndex(d.t))


def order_stats(tr, split=SPLIT):
    """tr: completed trades of one order with sym, t_in, pnl7, pnl15, pnl23, R. R_budget = pnl / 8."""
    import pandas as pd
    n = len(tr)
    if not n: return dict(n=0, n_early=0, n_late=0)
    e = tr[tr.t_in < pd.Timestamp(split)]; l = tr[tr.t_in >= pd.Timestamp(split)]
    by = tr.groupby('sym').pnl15.sum(); best = by.idxmax(); ex = tr[tr.sym != best]
    m = lambda x, c: float(x[c].mean() / RISK_USD) if len(x) else None
    return dict(n=n, n_early=len(e), n_late=len(l), meanR7=m(tr, 'pnl7'), meanR15=m(tr, 'pnl15'), meanR23=m(tr, 'pnl23'),
                meanR15_early=m(e, 'pnl15'), meanR15_late=m(l, 'pnl15'), best_coin=best, n_ex_best=len(ex),
                meanR15_ex_best=m(ex, 'pnl15'), sumR15=float(tr.pnl15.sum() / RISK_USD), meanR_actual=float(tr.R.mean()),
                coins_traded=int(tr.sym.nunique()), coins_positive=int((by > 0).sum()))


def gates(per_order):
    """Rule v1 gates: medians over the orders; >= 30 closes per half in order 0. A missing value (no trades) fails."""
    import numpy as np
    def med(k):
        v = [o.get(k) for o in per_order]
        return None if any(x is None for x in v) else float(np.median(v))
    M = {k: med(k) for k in ('n', 'meanR7', 'meanR15', 'meanR23', 'meanR15_early', 'meanR15_late', 'meanR15_ex_best')}
    pos = lambda k: M[k] is not None and M[k] > 0
    g = dict(trades=M['n'] is not None and M['n'] >= 100, meanR15=pos('meanR15'), early=pos('meanR15_early'), late=pos('meanR15_late'),
             ex_best_coin=pos('meanR15_ex_best'), meanR23=M['meanR23'] is not None and M['meanR23'] >= 0,
             order0_30_per_half=per_order[0].get('n_early', 0) >= 30 and per_order[0].get('n_late', 0) >= 30)
    return M, g


def worker(a):
    import warnings; warnings.filterwarnings('ignore')
    import numpy as np, pandas as pd
    src = a.worker
    sys.path[:0] = [src, os.path.join(src, 'research')]
    import backtest as BT, strategies as S
    case = CASES['cases'][a.case]
    key, tf = case['key'], case['tf']
    st = S.STRATEGIES[key]
    assert (st.get('tf') or '4h') == tf and st['sides'] == case['side'], 'registered tf/side differ from the frozen case'
    reg = {k: v for k, v in st['mgmt'].items()}
    assert all(reg.get(k) == v for k, v in case['management'].items() if k != 'exit') and \
        set(reg) == set(case['management']) - {'exit'}, f'registered mgmt {reg} differs from the frozen case'
    coins = a.universe.split(',') if a.universe else case_coins(a.case)
    bar_h = RS.BAR_H[tf]; warm = 60 if tf == '1d' else 220
    raw1 = {s: RS.load_1h(a.data, s) for s in [BTC] + coins}
    rawtf = {s: RS.to_tf(d, tf) for s, d in raw1.items()}
    tl = pd.DatetimeIndex(rawtf[BTC].t)
    pad = {s: d.set_index('t').reindex(tl).rename_axis('t').reset_index() for s, d in rawtf.items()}
    book = BT.Book(pad)                                   # the one-account book on BTC's full timeline
    sig_rows = []
    for s in coins:
        d = rawtf[s]
        b2 = BT.Book({BTC: rawtf[BTC][rawtf[BTC].t >= d.t.iloc[0]].reset_index(drop=True), s: d})   # real history only
        if len(b2.t) != len(d): raise RuntimeError(f'{s}: {len(d) - len(b2.t)} bars are not on BTC\'s timeline')
        g = b2.raw_sig(key, s, None)
        pos = tl.get_indexer(pd.DatetimeIndex(b2.t))
        if (pos < 0).any(): raise RuntimeError(f'{s}: timeline mismatch')
        liq = liquidity_1h(raw1[s]).reindex(pd.DatetimeIndex(b2.t) + pd.Timedelta(hours=bar_h - 1)).fillna(False).values.astype(bool)
        elig = liq & (np.arange(len(d)) >= warm) & (b2.t.values >= np.datetime64(T0))
        full = {}
        for k in ('le', 'se', 'lx', 'sx'):
            x = np.zeros(len(tl), bool); x[pos] = g[k] & elig if k in ('le', 'se') else g[k]; full[k] = x
        if 'atr' in g:
            x = np.full(len(tl), np.nan); x[pos] = g['atr']; full['atr'] = x
        book._sig[(key, s, str(None))] = full
        ind = b2.d[s].set_index(pd.DatetimeIndex(b2.t)).reindex(tl)
        ind['t'] = tl; ind = ind.reset_index(drop=True)
        book.d[s] = ind
        book.arr[s] = {k: ind[k].values for k in ('o', 'h', 'l', 'c', 'atr', 'e50', 'st')}
        side_key = 'le' if case['side'] == 'long' else 'se'
        for i in np.flatnonzero(g[side_key]):
            sig_rows.append((s, str(b2.t.iloc[i]), case['side'], bool(elig[i])))
    rules = json.load(open(os.path.join(src, 'data', 'exchange_rules_testnet.json'), encoding='utf-8'))
    T = pd.to_datetime(book.t.values)

    def one(order, scen):
        log = []
        tr, cv = BT.run(book, [dict(key=key, share=1.0, risk=RISK_USD / START, max_pos=1, symbols=order)], start=START,
                        max_lev=CAP_USD / START, daily_halt=0.08, t0=T0, warmup=warm, fund_per_bar=0.00005 * bar_h / 4,
                        pessimistic=scen, exchange_rules=rules, fill_log=log)
        feas = {k: v for k, v in (cv.attrs.get('feasibility') or {}).items() if k in ('attempts', 'executed', 'rule_blocked', 'skipped')}
        if not len(tr): return pd.DataFrame(columns=['sym', 't_in', 'pnl7', 'pnl15', 'pnl23', 'R']), feas
        tr = tr.copy()
        tr['t_in'] = T[tr.i_in.values]; tr['t_out'] = T[tr.i_out.values]
        turn = {}
        for f in log:
            k = (f['sym'], f['pos_i']); turn[k] = turn.get(k, 0.0) + abs(f['ref'] * f['qty'])
        tr['turnover'] = [turn.get(k, np.nan) for k in zip(tr.sym, tr.i_in)]
        if tr.turnover.isna().any(): raise RuntimeError('trade without fills in the fill log')
        tr['pnl7'] = tr.pnl; tr['pnl15'] = tr.pnl - tr.turnover * 0.0008; tr['pnl23'] = tr.pnl - tr.turnover * 0.0016
        return tr, feas

    os.makedirs(a.out, exist_ok=True)
    res = dict(path=[], worst=[])
    for k, order in enumerate(orders(coins, a.orders)):
        for scen in ('path', 'worst'):
            tr, feas = one(order, scen)
            stt = order_stats(tr); stt['order'] = k; stt['feasibility'] = feas
            res[scen].append(stt)
            if k == 0:
                tr.to_csv(os.path.join(a.out, f'{a.case}_order0_{scen}_trades.csv'), index=False, encoding='utf-8')
                if scen == 'path' and len(tr):
                    r = (tr.sort_values('t_out').pnl15 / RISK_USD).cumsum()
                    extra = dict(maxdd_R15_closed=float((r.cummax() - r).max()), why=tr.why.value_counts().to_dict(),
                                 per_coin={s_: dict(n=int(len(x)), sumR15=round(float(x.pnl15.sum() / RISK_USD), 3)) for s_, x in tr.groupby('sym')},
                                 closes_per_week=float(len(tr) / ((T[-1] - pd.Timestamp(T0)).days / 7)),
                                 max_notional=float(tr.turnover.max() / 2))
        print(f'[{a.case}] order {k}: path n={res["path"][-1]["n"]} R15={res["path"][-1].get("meanR15")}', flush=True)
    M, g = gates(res['path']); Mw, gw = gates(res['worst'])
    r15 = [o.get('meanR15') for o in res['path'] if o.get('meanR15') is not None]
    pd.DataFrame(sig_rows, columns=['sym', 'decision_bar_open_utc', 'side', 'eligible']).to_csv(
        os.path.join(a.out, f'{a.case}_signals.csv'), index=False, encoding='utf-8')
    out = dict(case=a.case, frozen_case=case, coins=coins, n_coins=len(coins), orders=a.orders, universe='smoke' if a.universe else 'frozen',
               status=('TRANSFER_SCREEN_PASS' if all(g.values()) else 'FAIL'), label=case['label'], gates=g, medians=M,
               meanR15_p10=float(np.percentile(r15, 10)) if r15 else None, meanR15_min=min(r15) if r15 else None,
               meanR15_max=max(r15) if r15 else None,
               orders_passing_all_but_order0_gate=sum(1 for o in res['path'] if all(v for kk, v in gates([o])[1].items() if kk != 'order0_30_per_half')),
               worst_path_diagnostic=dict(medians=Mw, gates=gw), order0=dict(res['path'][0], **(extra if res['path'][0]['n'] else {})),
               signals=dict(raw=len(sig_rows), eligible=sum(1 for r in sig_rows if r[3])),
               per_order=res, code_sha256={f: RS.sha(os.path.join(src, f)) for f in ('strategies.py', 'backtest.py')},
               cases_sha256=RS.sha(os.path.join(HERE, 'sieve_v1_cases.json')), runner_sha256=RS.sha(os.path.abspath(__file__)),
               screen_runner_sha256=RS.sha(os.path.join(HERE, 'run_screen.py')))
    json.dump(out, open(os.path.join(a.out, f'{a.case}_result.json'), 'w', encoding='utf-8'), indent=1, default=str)
    print('DONE', a.case, out['status'], g, {k: (round(v, 4) if v is not None else None) for k, v in M.items()}, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('data'); ap.add_argument('out'); ap.add_argument('--case', required=True, choices=sorted(CASES['cases']))
    ap.add_argument('--orders', type=int, default=40); ap.add_argument('--universe'); ap.add_argument('--worker')
    a = ap.parse_args()
    if a.worker: return worker(a)
    os.makedirs(a.out, exist_ok=True)
    coins = a.universe.split(',') if a.universe else case_coins(a.case)
    msha, fsha, listed = RS.check_data(a.data, not a.universe, [BTC] + coins)
    src = RS.export(CASES['cases'][a.case]['source'], a.out)
    cmd = [sys.executable, os.path.abspath(__file__), a.data, a.out, '--case', a.case, '--orders', str(a.orders), '--worker', src]
    if a.universe: cmd += ['--universe', a.universe]
    subprocess.run(cmd, check=True)
    json.dump(dict(data_manifest_sha256=msha, csv_sha256=fsha, csv_in_manifest=listed),
              open(os.path.join(a.out, f'{a.case}_inputs.json'), 'w', encoding='utf-8'), indent=1)


if __name__ == '__main__':
    main()
