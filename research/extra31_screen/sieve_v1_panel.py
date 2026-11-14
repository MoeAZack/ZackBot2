"""SIEVE-V1 (panel form): rule v1 for cases that need the cross-section (ranks, breadth, BTC panel) or both sides. RESEARCH ONLY.

Same rule, sizing, costs, eligibility, orders and gates as sieve_v1.py (imported from it, not restated). The only difference
is how a coin's signals are computed, so that late listings neither invent candles nor lose the cross-section:
  - indicators: the source's own strategies.indicators on each coin's real history only;
  - context: the source's own strategies.build_context over BTC + the case's coins on BTC's timeline, where a coin is simply
    absent (NaN) before its data starts, which is what build_context is written for;
  - signals: the unchanged registered function on the coin's real rows with that context cut to the same rows;
  - CP modules get their runner hooks (set_panel / set_btc) with the real frames of BTC + the case's coins.
Cases live in sieve_v1_panel_cases.json.
--max-pos 3|5 runs the disclosed post-hoc multi-position variant frozen in sieve_v1_mp_variant.json (default 1 = rule v1,
unchanged behaviour and file names).
  python research/extra31_screen/sieve_v1_panel.py <clean_csv_dir> <out_dir> --case NAME [--orders 40] [--universe S1,S2] [--max-pos 1|3|5]
"""
import sys, os, json, subprocess, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_screen as RS, sieve_v1 as SV

CASES = json.load(open(os.path.join(HERE, 'sieve_v1_panel_cases.json'), encoding='utf-8'))
VARIANT = json.load(open(os.path.join(HERE, 'sieve_v1_mp_variant.json'), encoding='utf-8'))
BTC = RS.BTC


def case_coins(case):
    c = CASES['cases'][case]
    return sorted(s for s in RS.EXTRA31 if s not in c.get('prior_used', []))


def cut_ctx(ctx, first, n):
    """A coin's context on BTC's timeline -> the same context on the coin's own rows [first:]."""
    import numpy as np, pandas as pd
    out = {}
    for k, v in ctx.items():
        if isinstance(v, pd.Series) and len(v) == n: out[k] = v.iloc[first:].reset_index(drop=True)
        elif isinstance(v, np.ndarray) and len(v) == n: out[k] = v[first:]
        else: out[k] = v
    return out


def worker(a):
    import warnings; warnings.filterwarnings('ignore')
    import numpy as np, pandas as pd
    src = a.worker
    sys.path[:0] = [src, os.path.join(src, 'research')]
    import backtest as BT, strategies as S
    case = CASES['cases'][a.case]
    mods = []
    for m in RS.CONTRACT['sources'][case['source']]['modules']:
        M = __import__(m); M.register(); mods.append(M)
    key, tf = case['key'], case['tf']
    mp = a.max_pos; tag = a.case if mp == 1 else f'{a.case}_mp{mp}'
    if mp != 1: assert mp in VARIANT['max_pos'] and a.case in VARIANT['cases'], 'not a frozen variant case'
    st = S.STRATEGIES[key]
    assert (st.get('tf') or '4h') == tf and st['sides'] == case['side'], 'registered tf/side differ from the frozen case'
    assert dict(st['mgmt']) == case['management'] and dict(st.get('params') or {}) == case['params'], \
        f'registered mgmt/params {st["mgmt"]} {st.get("params")} differ from the frozen case'
    coins = a.universe.split(',') if a.universe else case_coins(a.case)
    coins = [s for s in coins if s not in st.get('exclude', [])]
    bar_h = RS.BAR_H[tf]; warm = 60 if tf == '1d' else 220
    raw1 = {s: RS.load_1h(a.data, s) for s in [BTC] + coins}
    rawtf = {s: RS.to_tf(d, tf) for s, d in raw1.items()}
    tl = pd.DatetimeIndex(rawtf[BTC].t); n = len(tl)
    for M in mods:
        if hasattr(M, 'set_panel'): M.set_panel(rawtf)
        if hasattr(M, 'set_btc'): M.set_btc(rawtf[BTC])
    real, first, padded = {}, {}, {}
    for s, d in rawtf.items():
        ind = S.indicators(d.drop_duplicates('t').sort_values('t').reset_index(drop=True))
        pos = tl.get_indexer(pd.DatetimeIndex(ind.t))
        if (pos < 0).any() or (np.diff(pos) != 1).any(): raise RuntimeError(f'{s}: bars are not a contiguous run of BTC\'s timeline')
        real[s], first[s] = ind, int(pos[0])
        p = ind.set_index(pd.DatetimeIndex(ind.t)).reindex(tl); p['t'] = tl
        padded[s] = p.reset_index(drop=True)
    ctx = S.build_context(padded, BTC)
    book = BT.Book({s: d.set_index('t').reindex(tl).rename_axis('t').reset_index() for s, d in rawtf.items()})
    book.ctx = ctx
    sig_rows = []
    for s in coins:
        d, f0 = real[s], first[s]
        g = S.signals(key, d, cut_ctx(ctx[s], f0, n), None, mask_sides=False)
        liq = SV.liquidity_1h(raw1[s]).reindex(pd.DatetimeIndex(d.t) + pd.Timedelta(hours=bar_h - 1)).fillna(False).values.astype(bool)
        elig = liq & (np.arange(len(d)) >= warm) & (d.t.values >= np.datetime64(SV.T0))
        full = {}
        for k in ('le', 'se', 'lx', 'sx'):
            x = np.zeros(n, bool); x[f0:f0 + len(d)] = (g[k] & elig) if k in ('le', 'se') else g[k]; full[k] = x
        if 'atr' in g:
            x = np.full(n, np.nan); x[f0:f0 + len(d)] = g['atr']; full['atr'] = x
        book._sig[(key, s, str(None))] = full
        book.d[s] = padded[s]
        book.arr[s] = {k: padded[s][k].values for k in ('o', 'h', 'l', 'c', 'atr', 'e50', 'st')}
        for side, k in (('long', 'le'), ('short', 'se')):
            if case['side'] in (side, 'both'):
                for i in np.flatnonzero(g[k]): sig_rows.append((s, str(d.t.iloc[i]), side, bool(elig[i])))
    rules = json.load(open(os.path.join(src, 'data', 'exchange_rules_testnet.json'), encoding='utf-8'))
    T = pd.to_datetime(book.t.values)

    def one(order, scen):
        log = []
        tr, cv = BT.run(book, [dict(key=key, share=1.0, risk=SV.RISK_USD / SV.START, max_pos=mp, symbols=order)], start=SV.START,
                        max_lev=SV.CAP_USD / SV.START, daily_halt=0.08, t0=SV.T0, warmup=warm, fund_per_bar=0.00005 * bar_h / 4,
                        pessimistic=scen, exchange_rules=rules, fill_log=log)
        feas = {k: v for k, v in (cv.attrs.get('feasibility') or {}).items() if k in ('attempts', 'executed', 'rule_blocked', 'skipped')}
        if not len(tr): return pd.DataFrame(columns=['sym', 'side', 't_in', 'pnl7', 'pnl15', 'pnl23', 'R']), feas
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
    res, extra = dict(path=[], worst=[]), {}
    for k, order in enumerate(SV.orders(coins, a.orders)):
        for scen in ('path', 'worst'):
            tr, feas = one(order, scen)
            stt = SV.order_stats(tr); stt['order'] = k; stt['feasibility'] = feas
            res[scen].append(stt)
            if k == 0:
                tr.to_csv(os.path.join(a.out, f'{tag}_order0_{scen}_trades.csv'), index=False, encoding='utf-8')
                if scen == 'path' and len(tr):
                    r = (tr.sort_values('t_out').pnl15 / SV.RISK_USD).cumsum()
                    ev = sorted([(int(i), 1) for i in tr.i_in] + [(int(i), -1) for i in tr.i_out], key=lambda e: (e[0], e[1]))
                    cur = peak = 0
                    for _, dlt in ev: cur += dlt; peak = max(peak, cur)
                    extra = dict(max_concurrent_closed_trades=peak, maxdd_R15_closed=float((r.cummax() - r).max()), why=tr.why.value_counts().to_dict(),
                                 long_n=int((tr.side == 1).sum()), short_n=int((tr.side == -1).sum()),
                                 long_sumR15=float(tr.pnl15[tr.side == 1].sum() / SV.RISK_USD),
                                 short_sumR15=float(tr.pnl15[tr.side == -1].sum() / SV.RISK_USD),
                                 per_coin={s_: dict(n=int(len(x)), sumR15=round(float(x.pnl15.sum() / SV.RISK_USD), 3)) for s_, x in tr.groupby('sym')},
                                 closes_per_week=float(len(tr) / ((T[-1] - pd.Timestamp(SV.T0)).days / 7)))
        print(f'[{tag}] order {k}: path n={res["path"][-1]["n"]} R15={res["path"][-1].get("meanR15")}', flush=True)
    M, g = SV.gates(res['path']); Mw, gw = SV.gates(res['worst'])
    r15 = [o.get('meanR15') for o in res['path'] if o.get('meanR15') is not None]
    pd.DataFrame(sig_rows, columns=['sym', 'decision_bar_open_utc', 'side', 'eligible']).to_csv(
        os.path.join(a.out, f'{tag}_signals.csv'), index=False, encoding='utf-8')
    out = dict(case=a.case, frozen_case=case, coins=coins, n_coins=len(coins), orders=a.orders, universe='smoke' if a.universe else 'frozen',
               max_pos=mp,
               status=(('TRANSFER_SCREEN_PASS' if mp == 1 else f'POST_HOC_VARIANT_MP{mp}_PASS') if all(g.values()) else 'FAIL'),
               label=case['label'] if mp == 1 else VARIANT['label'] + ' | ' + case['label'], gates=g, medians=M,
               meanR15_p10=float(np.percentile(r15, 10)) if r15 else None, meanR15_min=min(r15) if r15 else None,
               meanR15_max=max(r15) if r15 else None,
               orders_passing_all_but_order0_gate=sum(1 for o in res['path'] if all(v for kk, v in SV.gates([o])[1].items() if kk != 'order0_30_per_half')),
               worst_path_diagnostic=dict(medians=Mw, gates=gw), order0=dict(res['path'][0], **extra),
               signals=dict(raw=len(sig_rows), eligible=sum(1 for r in sig_rows if r[3])), per_order=res,
               code_sha256={f: RS.sha(os.path.join(src, p)) for f, p in [('strategies.py', 'strategies.py'), ('backtest.py', 'backtest.py')] +
                            [(m + '.py', f'research/{m}.py') for m in RS.CONTRACT['sources'][case['source']]['modules']]},
               cases_sha256=RS.sha(os.path.join(HERE, 'sieve_v1_panel_cases.json')),
               variant_sha256=RS.sha(os.path.join(HERE, 'sieve_v1_mp_variant.json')), runner_sha256=RS.sha(os.path.abspath(__file__)),
               sieve_v1_sha256=RS.sha(os.path.join(HERE, 'sieve_v1.py')))
    json.dump(out, open(os.path.join(a.out, f'{tag}_result.json'), 'w', encoding='utf-8'), indent=1, default=str)
    print('DONE', tag, out['status'], g, {k: (round(v, 4) if v is not None else None) for k, v in M.items()}, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('data'); ap.add_argument('out'); ap.add_argument('--case', required=True, choices=sorted(CASES['cases']))
    ap.add_argument('--orders', type=int, default=40); ap.add_argument('--universe'); ap.add_argument('--worker'); ap.add_argument('--max-pos', type=int, default=1, choices=[1, 3, 5])
    a = ap.parse_args()
    if a.worker: return worker(a)
    os.makedirs(a.out, exist_ok=True)
    coins = a.universe.split(',') if a.universe else case_coins(a.case)
    msha, fsha, listed = RS.check_data(a.data, not a.universe, [BTC] + coins)
    src = RS.export(CASES['cases'][a.case]['source'], a.out)
    cmd = [sys.executable, os.path.abspath(__file__), a.data, a.out, '--case', a.case, '--orders', str(a.orders), '--worker', src, '--max-pos', str(a.max_pos)]
    if a.universe: cmd += ['--universe', a.universe]
    subprocess.run(cmd, check=True)
    json.dump(dict(data_manifest_sha256=msha, csv_sha256=fsha, csv_in_manifest=listed),
              open(os.path.join(a.out, (a.case if a.max_pos == 1 else f'{a.case}_mp{a.max_pos}') + '_inputs.json'), 'w', encoding='utf-8'), indent=1)


if __name__ == '__main__':
    main()
