"""EXTRA31-SCREEN: run every catalogued strategy UNCHANGED on the 31 coins outside the original 9. RESEARCH ONLY.

The frozen setup and pass rule are in contract.json (same folder); this file only executes them.
  python research/extra31_screen/run_screen.py <clean_csv_dir> <out_dir> [--source NAME] [--keys k1,k2] [--universe S1,S2] [--jobs N]
The parent exports each source commit with `git archive` into <out_dir>/_src/<name> and runs one worker process per source
(each source has its own strategies.py / backtest.py, so they must not share a Python process). No network, no account,
no runtime. --universe exists for the smoke test on other coins; without it the frozen extra31 list is used.
"""
import sys, os, json, hashlib, subprocess, io, tarfile, traceback, argparse, glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONTRACT = json.load(open(os.path.join(HERE, 'contract.json'), encoding='utf-8'))
EXTRA31 = CONTRACT['universe']['extra31']
BTC = 'BTCUSDT'
T0 = '2022-08-26 06:00'
SPLIT = '2024-09-14 00:00'
START, RISK = 10000000.0, 0.0000008     # $8 per entry; equity this large keeps the risk at $8.00 whatever the P&L
BAR_H = {'1h': 1, '4h': 4, '1d': 24}
SKIP = {k: v for k, v in CONTRACT['skipped_already_tested_on_extra31'].items() if k != 'not_in_catalog'}
FIRST = ['ema_st', 'kalman_innovation_recovery_long_1h']
MIN_TRADES = 100


def sha(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


# ------------------------------------------------------------------ data
def load_1h(dirp, sym):
    import pandas as pd
    d = pd.read_csv(os.path.join(dirp, f'{sym}_1h.csv'))
    d['t'] = pd.to_datetime(d.t, unit='ms') if str(d.t.dtype).startswith(('int', 'float')) else pd.to_datetime(d.t)
    if d.t.duplicated().any(): raise ValueError(f'{sym}: duplicate timestamps')
    if not d.t.is_monotonic_increasing: raise ValueError(f'{sym}: timestamps not sorted')
    cols = ['t', 'o', 'h', 'l', 'c', 'v'] + (['qv'] if 'qv' in d else [])
    return d[cols].reset_index(drop=True)


def to_tf(d, tf):
    """UTC bins built from 1h; a bin needs ALL of its 1h bars, else it is dropped."""
    if tf == '1h': return d
    n = BAR_H[tf]
    g = d.set_index('t').resample('4h' if tf == '4h' else '1D', label='left', closed='left')
    agg = dict(o='first', h='max', l='min', c='last', v='sum')
    if 'qv' in d: agg['qv'] = 'sum'
    out = g.agg(agg)
    out = out[(g.size() == n).values].dropna(subset=['o', 'h', 'l', 'c']).reset_index()
    return out


def cohorts(raw):
    """Listing cohorts, so that no candle is ever invented for a coin that was not listed yet.
    raw: {sym: frame} of one timeframe. Yields (start, traded, frames): `traded` are the coins whose first bar is `start`;
    `frames` holds BTC and every coin already listed at `start`, each cut to t >= start. backtest.Book then works on the
    common timestamps, which begin at `start`; older coins give the cross-sectional context of that moment."""
    first = {s: d.t.iloc[0] for s, d in raw.items() if len(d)}
    for start in sorted({first[s] for s in first if s != BTC}):
        traded = sorted(s for s in first if s != BTC and first[s] == start)
        frames = {s: d[d.t >= start].reset_index(drop=True) for s, d in raw.items() if s in first and first[s] <= start}
        if BTC not in frames: raise ValueError(f'BTC starts after {traded}')
        yield start, traded, frames


# ------------------------------------------------------------------ verdict
def verdict(tr, split=SPLIT, min_trades=MIN_TRADES):
    """tr: DataFrame with sym, t_in, R7, R15, R23. Returns (status, gates dict, stats dict). The frozen pass rule."""
    import pandas as pd
    n = len(tr)
    if n == 0:
        return 'FAIL', dict(trades=False), dict(n=0)
    early = tr[tr.t_in < pd.Timestamp(split)]; late = tr[tr.t_in >= pd.Timestamp(split)]
    by = tr.groupby('sym').R15.sum()
    best = by.idxmax()
    ex = tr[tr.sym != best]
    m = lambda x, c: float(x[c].mean()) if len(x) else None
    st = dict(n=n, meanR7=m(tr, 'R7'), meanR15=m(tr, 'R15'), meanR23=m(tr, 'R23'), sumR15=float(tr.R15.sum()),
              n_early=len(early), n_late=len(late), meanR15_early=m(early, 'R15'), meanR15_late=m(late, 'R15'),
              best_coin=best, best_coin_sumR15=float(by.max()), n_ex_best=len(ex), meanR15_ex_best=m(ex, 'R15'),
              coins_traded=int(tr.sym.nunique()), coins_positive=int((by > 0).sum()), win_pct=float((tr.R7 > 0).mean() * 100),
              usd_15bp_at_8=float(tr.R15.sum() * 8.0))
    g = dict(trades=n >= min_trades, meanR15=st['meanR15'] > 0, meanR23=st['meanR23'] >= 0,
             halves=bool(len(early) and len(late) and st['meanR15_early'] > 0 and st['meanR15_late'] > 0),
             ex_best_coin=bool(len(ex) and st['meanR15_ex_best'] > 0))
    return ('PASS' if all(g.values()) else 'FAIL'), g, st


# ------------------------------------------------------------------ worker (runs inside one exported source tree)
def run_book(BT, S, key, book, traded, rules):
    """One strategy on one cohort book. Returns (seasoned trades with R at 7/15/23 bp, raw count, dropped count, feasibility)."""
    import numpy as np, pandas as pd
    st = S.STRATEGIES[key]
    tf = st.get('tf') or '4h'
    bar_h = BAR_H[tf]
    warm = 60 if tf == '1d' else 220
    syms = [s for s in traded if s not in st.get('exclude', [])]
    if not syms: return None, 0, 0, {}
    log = []
    tr, cv = BT.run(book, [dict(key=key, share=1.0, risk=RISK, max_pos=99, symbols=syms)], start=START, max_lev=10,
                    daily_halt=0.08, t0=T0, warmup=warm, fund_per_bar=0.00005 * bar_h / 4, pessimistic='path',
                    exchange_rules=rules, fill_log=log)
    feas = {k: v for k, v in (cv.attrs.get('feasibility') or {}).items() if k in ('attempts', 'executed', 'rule_blocked')}
    if not len(tr): return None, 0, 0, feas
    T = pd.to_datetime(book.t.values)
    tr = tr.copy()
    tr['t_in'] = T[tr.i_in.values]; tr['t_out'] = T[tr.i_out.values]
    turn, erisk = {}, {}
    for f in log:
        k = (f['sym'], f['pos_i'])
        turn[k] = turn.get(k, 0.0) + abs(f['ref'] * f['qty'])
        if f.get('kind') == 'entry' and f.get('risk'): erisk[k] = float(f['risk'])
    ks = list(zip(tr.sym, tr.i_in))
    tr['turnover'] = [turn.get(k, np.nan) for k in ks]
    if tr.turnover.isna().any(): raise RuntimeError('trade without fills in the fill log')
    with np.errstate(divide='ignore', invalid='ignore'):
        r = (tr.pnl / tr.R).abs()
    r = r.where(np.isfinite(r) & (r > 0), pd.Series([erisk.get(k, np.nan) for k in ks], index=tr.index))
    if r.isna().any(): raise RuntimeError('trade without a risk basis')
    tr['risk_usd'] = r
    tr['R7'] = tr.pnl / r; tr['R15'] = (tr.pnl - tr.turnover * 0.0008) / r; tr['R23'] = (tr.pnl - tr.turnover * 0.0016) / r
    need = max(warm, int(720 / bar_h))           # seasoning: the coin's own first `need` bars are never traded
    keep = (tr.i_in >= need).values
    return tr[keep].reset_index(drop=True), int(len(tr)), int((~keep).sum()), feas


def worker(a):
    import warnings; warnings.filterwarnings('ignore')
    src = a.worker
    sys.path[:0] = [src, os.path.join(src, 'research')]
    import pandas as pd
    import backtest as BT, strategies as S
    spec = CONTRACT['sources'][a.source]
    base_keys = set(S.STRATEGIES)
    mods = []
    for m in spec['modules']:
        M = __import__(m); M.register(); mods.append(M)
    keys = [k for k in S.STRATEGIES if k not in base_keys] if mods else list(S.STRATEGIES)
    if a.source == 'c254':                       # only the keys that master does not have
        keys = [k for k in keys if k not in set(json.load(open(os.path.join(a.out, '_master_keys.json'))))]
    if a.keys: keys = [k for k in keys if k in a.keys.split(',')]
    keys = [k for k in FIRST if k in keys] + [k for k in keys if k not in FIRST]
    ci, cn = [int(x) for x in a.chunk.split('/')]
    keys = keys[ci::cn]
    tag = a.source if cn == 1 else f'{a.source}.{ci}'
    universe = a.universe.split(',') if a.universe else EXTRA31
    raw1 = {s: load_1h(a.data, s) for s in [BTC] + [u for u in universe if u != BTC]}
    rules = json.load(open(os.path.join(src, 'data', 'exchange_rules_testnet.json'), encoding='utf-8'))
    hashes = {f: sha(os.path.join(src, p)) for f, p in
              [('strategies.py', 'strategies.py'), ('backtest.py', 'backtest.py')] + [(m + '.py', f'research/{m}.py') for m in spec['modules']]}
    os.makedirs(os.path.join(a.out, 'trades'), exist_ok=True)
    rows = {}
    for k in keys:
        st = S.STRATEGIES[k]
        tf = st.get('tf') or '4h'
        rows[k] = dict(key=k, source=a.source, commit=spec['commit'], name=st.get('name'), tf=tf, sides=st.get('sides'))
        if k in SKIP: rows[k].update(status='SKIPPED_ALREADY_TESTED', note=SKIP[k])
        elif tf == '15m': rows[k].update(status='NO_DATA_15M')
        elif tf not in BAR_H: rows[k].update(status='ERROR', error=f'timeframe {tf}')
    acc = {k: dict(tr=[], raw=0, dropped=0, feas={}) for k in keys if 'status' not in rows[k]}
    for tf in sorted({rows[k]['tf'] for k in acc}):
        rawtf = {s: to_tf(d, tf) for s, d in raw1.items()}
        for start, traded, frames in cohorts(rawtf):
            book = BT.Book(frames)
            for M in mods:                       # the CP modules read a cross-sectional panel / BTC frame set by their runner
                if hasattr(M, 'set_panel'): M.set_panel(frames)
                if hasattr(M, 'set_btc'): M.set_btc(frames[BTC])
            for k in [k for k in acc if rows[k]['tf'] == tf and 'status' not in rows[k]]:
                try:
                    tr, nraw, ndrop, feas = run_book(BT, S, k, book, traded, rules)
                    A = acc[k]; A['raw'] += nraw; A['dropped'] += ndrop
                    for f, v in feas.items(): A['feas'][f] = A['feas'].get(f, 0) + (v if isinstance(v, (int, float)) else 0)
                    if tr is not None and len(tr): A['tr'].append(tr)
                except (KeyError, AttributeError) as e:  # the rule reads an input this 1h OHLCV data does not hold
                    rows[k].update(status='NO_DATA_INPUT', error=f'{type(e).__name__}: {e}', trace=traceback.format_exc()[-1500:])
                except Exception as e:
                    rows[k].update(status='ERROR', error=f'{type(e).__name__}: {e}', trace=traceback.format_exc()[-1500:])
            print(f'[{tag}] {tf} cohort {start} traded={len(traded)} book={len(frames)} done', flush=True)
            del book
    out = []
    for k in keys:
        row = rows[k]
        if 'status' not in row:
            A = acc[k]
            cols = ['sleeve', 'sym', 'side', 'i_in', 'i_out', 'pnl', 'R', 'why', 't_in', 't_out', 'turnover', 'risk_usd', 'R7', 'R15', 'R23']
            tr = pd.concat(A['tr'], ignore_index=True).sort_values(['t_in', 'sym']).reset_index(drop=True) if A['tr'] else pd.DataFrame(columns=cols)
            s_, g, stt = verdict(tr)
            row.update(status=s_, gates=g, stats=stt, raw_trades=A['raw'], dropped_seasoning=A['dropped'], feasibility=A['feas'],
                       why=tr.why.value_counts().to_dict() if len(tr) else {},
                       per_coin={s: dict(n=int(len(x)), sumR15=round(float(x.R15.sum()), 3)) for s, x in tr.groupby('sym')},
                       long_n=int((tr.side == 1).sum()) if len(tr) else 0, short_n=int((tr.side == -1).sum()) if len(tr) else 0)
            if len(tr): tr.to_csv(os.path.join(a.out, 'trades', f'{k}.csv'), index=False, encoding='utf-8')
        out.append(row)
        s = row.get('stats') or {}
        print(f"[{tag}] {k}: {row['status']} n={s.get('n')} R15={s.get('meanR15')}", flush=True)
    json.dump(dict(source=a.source, commit=spec['commit'], code_sha256=hashes, universe=universe, results=out),
              open(os.path.join(a.out, f'results_{tag}.json'), 'w', encoding='utf-8'), indent=1, default=str)


# ------------------------------------------------------------------ parent
def export(name, out):
    spec = CONTRACT['sources'][name]
    dst = os.path.join(out, '_src', name)
    os.makedirs(dst, exist_ok=True)
    names = subprocess.run(['git', '-C', ROOT, 'ls-tree', '-r', '--name-only', spec['commit']], check=True, capture_output=True,
                           text=True).stdout.splitlines()
    want = [n for n in names if n.endswith('.py') and ('/' not in n or (n.startswith('research/') and n.count('/') == 1))]
    want.append('data/exchange_rules_testnet.json')
    blob = subprocess.run(['git', '-C', ROOT, 'archive', spec['commit']] + want, check=True, capture_output=True).stdout
    tarfile.open(fileobj=io.BytesIO(blob)).extractall(dst, filter='data')
    return dst


def check_data(dirp, strict):
    mp = os.path.join(dirp, CONTRACT['data']['manifest'])
    got = sha(mp) if os.path.exists(mp) else None
    if strict and got != CONTRACT['data']['manifest_sha256']:
        raise SystemExit(f'data manifest sha256 {got} != frozen {CONTRACT["data"]["manifest_sha256"]}')
    return got


def summarize(out):
    rows = []
    for name in CONTRACT['sources']:
        got = []
        for p in sorted(glob.glob(os.path.join(out, f'results_{name}*.json'))):
            got += json.load(open(p, encoding='utf-8'))['results']
        rows += got
    rows.sort(key=lambda r: (r['key'] not in FIRST, r['status'] != 'PASS', -((r.get('stats') or {}).get('meanR15') or -99.0)))
    import csv
    cols = ['key', 'source', 'tf', 'sides', 'status', 'n', 'meanR7', 'meanR15', 'meanR23', 'meanR15_early', 'meanR15_late',
            'best_coin', 'meanR15_ex_best', 'coins_traded', 'coins_positive', 'usd_15bp_at_8', 'failed_gates', 'note']
    with open(os.path.join(out, 'summary.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(cols)
        for r in rows:
            s, g = r.get('stats') or {}, r.get('gates') or {}
            w.writerow([r['key'], r['source'], r.get('tf'), r.get('sides'), r['status']] +
                       [round(s[c], 4) if isinstance(s.get(c), float) else s.get(c) for c in cols[5:16]] +
                       ['|'.join(k for k, v in g.items() if not v), r.get('note') or r.get('error') or ''])
    cnt = {}
    for r in rows: cnt[r['status']] = cnt.get(r['status'], 0) + 1
    return rows, cnt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('data'); ap.add_argument('out')
    ap.add_argument('--source'); ap.add_argument('--keys'); ap.add_argument('--universe'); ap.add_argument('--worker')
    ap.add_argument('--jobs', type=int, default=1); ap.add_argument('--chunk', default='0/1')
    a = ap.parse_args()
    if a.worker: return worker(a)
    os.makedirs(a.out, exist_ok=True)
    msha = check_data(a.data, strict=not a.universe)
    names = [a.source] if a.source else list(CONTRACT['sources'])
    if 'c254' in names and 'master' not in names and not os.path.exists(os.path.join(a.out, '_master_keys.json')):
        names = ['master'] + names
    for name in names:
        src = export(name, a.out)
        for p in glob.glob(os.path.join(a.out, f'results_{name}*.json')): os.remove(p)
        if name == 'master':                     # the c254 workers need master's key list before they start
            code = "import sys, json; sys.path.insert(0, sys.argv[1]); import strategies as S; json.dump(sorted(S.STRATEGIES), open(sys.argv[2], 'w'))"
            subprocess.run([sys.executable, '-c', code, src, os.path.join(a.out, '_master_keys.json')], check=True)
        procs = []
        for i in range(a.jobs):
            cmd = [sys.executable, os.path.abspath(__file__), a.data, a.out, '--source', name, '--worker', src, '--chunk', f'{i}/{a.jobs}']
            if a.keys: cmd += ['--keys', a.keys]
            if a.universe: cmd += ['--universe', a.universe]
            procs.append(subprocess.Popen(cmd))
        if any(p.wait() for p in procs): raise SystemExit(f'worker failed for source {name}')
    rows, cnt = summarize(a.out)
    json.dump(dict(contract_sha256=sha(os.path.join(HERE, 'contract.json')), runner_sha256=sha(os.path.abspath(__file__)),
                   data_manifest_sha256=msha, universe=a.universe or 'extra31', counts=cnt, strategies=len(rows)),
              open(os.path.join(a.out, 'run_meta.json'), 'w', encoding='utf-8'), indent=1)
    print('DONE', cnt, flush=True)


if __name__ == '__main__':
    main()
