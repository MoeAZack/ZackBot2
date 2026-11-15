"""LEADFWD-01: forward book rows for six frozen LONGSCOUT leads on public data. RESEARCH ONLY.

Each row is a pure re-run from t_fwd (freeze.json) over CLOSED bars before the boundary B:
  1. verify the freeze, every lead's frozen cases file, the pinned base history and the forward fetch manifest;
  2. merge base history + forward fetch per coin (overlap must be identical; a forward gap cuts the coin, see freeze.json);
  3. per lead, run the UNCHANGED rule-v1 code path (longscout_run.worker -> sieve_v1_panel.worker, the lead's own source
     commit exported by git archive) with ONE change: backtest.run gets t0 = t_fwd, so the book starts flat at t_fwd;
  4. derive closed trades, open positions and signals since t_fwd; check every earlier row is reproduced exactly;
  5. write rows/<B_ms>/row.json + summary.md + receipts.json once (never rewritten).
No network, account, key, order, PAPER, runtime or scheduler action.
  python research/lead_fwd/run_fwd.py <fwd_fetch_root> <rows_root> --boundary-ms B [--fresh DIR] [--extra DIR] [--orders 40]
Rows before t_fwd (B <= t_fwd) are written under <rows_root>/dryrun/ and are pipeline checks, not forward evidence.
"""
import sys, os, json, hashlib, subprocess, argparse, shutil, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
X31 = os.path.join(ROOT, 'research', 'extra31_screen')
sys.path.insert(0, X31)
FREEZE = os.path.join(HERE, 'freeze.json')
FREEZE_SHA_FILE = os.path.join(HERE, 'freeze.sha256')
H = 3600000
COLS = ['o', 'h', 'l', 'c', 'v', 'qv']


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def load_freeze(check=True):
    fz = json.load(open(FREEZE, encoding='utf-8'))
    if check:
        want = open(FREEZE_SHA_FILE, encoding='utf-8').read().split()[0]
        if sha(FREEZE) != want: raise SystemExit('freeze.json differs from the committed freeze.sha256')
        for k, L in fz['leads'].items():
            if sha(os.path.join(ROOT, L['cases_file'])) != L['cases_sha256']: raise SystemExit(f'{k}: cases file differs from the freeze')
    return fz


# ------------------------------------------------------------------ data
def read_1h(path):
    """1h csv (t as open ms or a UTC datetime string) -> DataFrame with integer t (ms) and float o,h,l,c,v,qv.
    Timestamps must be strictly increasing in file order (FWD-BOOKS amendment A1 guard)."""
    import pandas as pd, numpy as np
    d = pd.read_csv(path)
    if str(d.t.dtype).startswith(('int', 'float')): t = d.t.astype('int64').values
    else: t = (pd.to_datetime(d.t).values.astype('datetime64[ms]').astype('int64'))
    if len(t) > 1 and not (np.diff(t) > 0).all():
        bad = int(np.flatnonzero(np.diff(t) <= 0)[0]) + 1
        raise ValueError(f'{os.path.basename(path)}: timestamps not strictly increasing at row {bad} (t={int(t[bad])})')
    out = pd.DataFrame({'t': t})
    for c in COLS:
        out[c] = d[c].astype(float).values if c in d else np.nan
    return out


def merge_coin(base, fwd, B):
    """base, fwd: frames from read_1h. Returns (merged frame, info). info['status']:
    OK       forward data reaches B-1h with no missing hour after the base;
    CUT      a missing hour (or an early end) after the base: merged is cut at the last bar before it;
    MISMATCH an overlapping bar differs (merged None);
    NO_FWD   no forward rows at all (merged = base, which ends long before B-1h: treated as CUT at the base end)."""
    import numpy as np, pandas as pd
    info = dict(base_last_ms=int(base.t.iloc[-1]) if len(base) else None)
    if fwd is None or not len(fwd):
        info.update(status='NO_FWD', cut_after_ms=info['base_last_ms'])
        return base.copy(), info
    base = base[base.t < B]
    fwd = fwd[fwd.t < B]
    ov = base.merge(fwd, on='t', suffixes=('_b', '_f'))
    info['overlap_bars'] = int(len(ov))
    for c in COLS:
        a, b = ov[c + '_b'].values, ov[c + '_f'].values
        both_nan = np.isnan(a) & np.isnan(b)
        diff = ~(both_nan | (a == b))
        if diff.any():
            j = int(np.flatnonzero(diff)[0])
            info.update(status='MISMATCH', column=c, t_ms=int(ov.t.iloc[j]), base=float(a[j]), fwd=float(b[j]), n_diff=int(diff.sum()))
            return None, info
    last = int(base.t.iloc[-1]) if len(base) else -1
    if len(base) and int(fwd.t.iloc[0]) > last + H:
        info.update(status='CUT', cut_after_ms=last, reason='forward data starts after a missing hour')
        return base.reset_index(drop=True), info
    m = pd.concat([base, fwd[fwd.t > last]], ignore_index=True)
    t = m.t.values
    start = int(np.searchsorted(t, last)) if len(base) else 0
    step = np.diff(t[start:])
    if (step != H).any():
        k = start + int(np.flatnonzero(step != H)[0])
        info.update(status='CUT', cut_after_ms=int(t[k]), reason='missing 1h bar in forward data')
        return m.iloc[:k + 1].reset_index(drop=True), info
    if int(t[-1]) < B - H:
        info.update(status='CUT', cut_after_ms=int(t[-1]), reason='forward data ends before the boundary')
        return m, info
    info['status'] = 'OK'
    return m, info


def write_1h(df, path):
    import pandas as pd
    x = df.copy()
    x['t'] = pd.to_datetime(x.t, unit='ms').dt.strftime('%Y-%m-%d %H:%M:%S')
    x.to_csv(path, index=False, encoding='utf-8', float_format=None)


def verify_base(fz, fresh_dir, extra_dir, coins):
    """Pinned base history: the fresh manifest sha and every fresh csv sha in it; the extra31 cache manifest (strict) and
    every csv's sha listed in that manifest's text. Returns {sym: path}."""
    import run_screen as RS
    bh = fz['base_history']
    man = os.path.join(fresh_dir, 'input_manifest.json')
    if sha(man) != bh['fresh_manifest_sha256']: raise SystemExit('fresh manifest sha256 differs from the freeze')
    m = json.load(open(man, encoding='utf-8'))
    paths = {}
    for s in ['BTCUSDT'] + fz['universe']['fresh134']:
        p = os.path.join(fresh_dir, 'clean', f'{s}_1h.csv')
        if sha(p) != m['symbols'][s]['sha256']: raise SystemExit(f'{s}: fresh csv sha differs from its manifest')
        paths[s] = p
    if RS.CONTRACT['data']['manifest_sha256'] != bh['extra_manifest_sha256']: raise SystemExit('contract extra31 manifest pin differs from the freeze')
    got, files, listed = RS.check_data(extra_dir, True, fz['universe']['extra31'])
    if listed != len(fz['universe']['extra31']): raise SystemExit(f'only {listed} of 31 extra csv shas are in the cache manifest')
    for s in fz['universe']['extra31']: paths[s] = os.path.join(extra_dir, f'{s}_1h.csv')
    assert sorted(paths) == sorted(coins), 'base paths do not cover the universe'
    return paths


def verify_fetch(fz, fdir, B, coins):
    mp = os.path.join(fdir, 'fetch_manifest.json')
    m = json.load(open(mp, encoding='utf-8'))
    if m['boundary_ms'] != B: raise SystemExit('fetch manifest boundary differs')
    if m['freeze_sha256'] != sha(FREEZE): raise SystemExit('forward data was fetched under a different freeze file')
    if m['start_ms'] != fz['forward_fetch']['start_ms']: raise SystemExit('fetch start differs from the freeze')
    paths = {}
    for s in coins:
        v = m['symbols'].get(s)
        p = os.path.join(fdir, f'{s}_1h.csv')
        if v is None: paths[s] = None; continue
        if sha(p) != v['sha256']: raise SystemExit(f'{s}: forward csv sha differs from the fetch manifest')
        paths[s] = p
    return sha(mp), paths


def build_data(base_paths, fwd_paths, B, work):
    """Merge every coin into work/<SYM>_1h.csv. Returns (coverage dict, problems list). BTC must be OK."""
    os.makedirs(work, exist_ok=True)
    cov, problems = {}, []
    for s in sorted(base_paths):
        base = read_1h(base_paths[s])
        fwd = read_1h(fwd_paths[s]) if fwd_paths.get(s) else None
        m, info = merge_coin(base, fwd, B)
        cov[s] = info
        if info['status'] == 'MISMATCH': problems.append(f'{s}: overlap mismatch {info}'); continue
        if s == 'BTCUSDT' and info['status'] != 'OK': problems.append(f'BTCUSDT incomplete: {info}')
        elif info['status'] != 'OK': problems.append(f'{s}: missing prices ({info["status"]}, last contiguous bar {info.get("cut_after_ms")})')
        write_1h(m, os.path.join(work, f'{s}_1h.csv'))
    return cov, problems


# ------------------------------------------------------------------ worker (one lead, inside its exported source)
def open_positions(tr, log, book, T, management):
    """Entries in the fill log without a closed trade = positions still open at the last closed bar."""
    import numpy as np
    closed = set(zip(tr.sym, tr.i_in)) if len(tr) else set()
    out = []
    for f in log:
        if f['kind'] != 'entry' or (f['sym'], f['pos_i']) in closed: continue
        s, side, ref, qty, R = f['sym'], f['side'], f['ref'], f['qty'], f['stop_dist']
        c = book.arr[s]['c']
        ok = np.flatnonzero(~np.isnan(c))
        li = int(ok[-1]) if len(ok) else -1
        mark = float(c[li])
        out.append(dict(sym=s, side=side, t_in=str(T[f['i']]), entry_ref=ref, qty=qty, risk_usd=f['risk'], stop_dist=R,
                        initial_stop=ref - side * R, target=ref + side * R * management['tp_r'], bars_held=len(T) - 1 - int(f['i']),
                        max_bars=management['max_bars'], mark_t=str(T[li]), mark=mark,
                        unrealized_R_before_costs=round(side * (mark - ref) * qty / 8.0, 4),
                        stale=bool(li < len(T) - 1)))
    return out


def worker(a):
    import warnings; warnings.filterwarnings('ignore')
    import numpy as np, pandas as pd
    fz = load_freeze(check=False)
    L = fz['leads'][a.case]
    cases = json.load(open(os.path.join(ROOT, L['cases_file']), encoding='utf-8'))
    import run_screen as RS, sieve_v1 as SV, sieve_v1_panel as SP, longscout_run as LR
    SP.CASES = cases; LR.CASES = cases
    RS.CONTRACT['sources'].update(cases['sources'])
    sys.path[:0] = [a.worker, os.path.join(a.worker, 'research')]
    import backtest as BT
    t_fwd = a.t_fwd or fz['t_fwd_utc']
    orig, CAP = BT.run, []

    def run(book, sleeves, **k):                    # the ONE change: the book starts flat at t_fwd
        k['t0'] = t_fwd
        tr, cv = orig(book, sleeves, **k)
        CAP.append(dict(scen=k.get('pessimistic'), tr=tr, log=list(k.get('fill_log') or []), book=book))
        return tr, cv
    BT.run = run
    LR.worker(argparse.Namespace(data=a.data, out=a.out, case=a.case, orders=a.orders, universe=a.universe, worker=a.worker, max_pos=1))
    res = json.load(open(os.path.join(a.out, f'{a.case}_result.json'), encoding='utf-8'))
    try:
        allt = pd.read_csv(os.path.join(a.out, f'{a.case}_all_orders_trades.csv.gz'))
    except pd.errors.EmptyDataError:
        allt = pd.DataFrame(columns=['sym', 'side', 't_in', 't_out', 'pnl7', 'pnl15', 'pnl23', 'R', 'why', 'order', 'scen'])
    if len(allt) and (pd.to_datetime(allt.t_in) < pd.Timestamp(t_fwd)).any(): raise RuntimeError('a trade entered before t_fwd')
    book = CAP[0]['book']
    T = pd.to_datetime(book.t.values)
    mg = cases['cases'][a.case]['management']
    opens = {}
    for i, c in enumerate(CAP):
        opens[(i // 2, c['scen'])] = open_positions(c['tr'], c['log'], book, T, mg)
    sig = pd.read_csv(os.path.join(a.out, f'{a.case}_signals.csv'))
    sig = sig[pd.to_datetime(sig.decision_bar_open_utc) >= pd.Timestamp(t_fwd)].sort_values(['decision_bar_open_utc', 'sym'])

    def closed(order, scen):
        x = allt[(allt.order == order) & (allt.scen == scen)].sort_values(['t_out', 'sym'])
        return [dict(sym=r.sym, side=int(r.side), t_in=str(r.t_in), t_out=str(r.t_out), why=r.why, R15=round(r.pnl15 / 8.0, 6),
                     R7=round(r.pnl7 / 8.0, 6), R23=round(r.pnl23 / 8.0, 6)) for r in x.itertuples()]

    def stats(rows):
        n = len(rows)
        if not n: return dict(n=0)
        r15 = np.array([r['R15'] for r in rows]); r23 = np.array([r['R23'] for r in rows])
        cum = np.cumsum(r15)
        return dict(n=n, sumR15=round(float(r15.sum()), 4), meanR15=round(float(r15.mean()), 4), meanR23=round(float(r23.mean()), 4),
                    wins=int((r15 > 0).sum()), maxdd_R15_closed=round(float((np.maximum.accumulate(np.r_[0, cum]) - np.r_[0, cum]).max()), 4))
    o0 = closed(0, 'path')
    per = [stats(closed(k, 'path')) for k in range(a.orders)]
    ns = [p['n'] for p in per]; ms = [p['meanR15'] for p in per if p['n']]
    out = dict(lead=a.case, label=L['label'], t_fwd=t_fwd, last_closed_bar_open_utc=str(T[-1]), source_commit=L['commit'],
               code_sha256=res.get('code_sha256'), cases_sha256=sha(os.path.join(ROOT, L['cases_file'])),
               primary=dict(book='order 0, path', closed=o0, stats=stats(o0), open=opens.get((0, 'path'), [])),
               worst_path_order0=dict(stats=stats(closed(0, 'worst')), open=opens.get((0, 'worst'), [])),
               orders=dict(n_orders=a.orders, median_closes=float(np.median(ns)), orders_with_closes=len(ms),
                           median_meanR15=float(np.median(ms)) if ms else None,
                           open_positions_by_order=[len(opens.get((k, 'path'), [])) for k in range(a.orders)]),
               signals=dict(n=int(len(sig)), n_eligible=int(sig.eligible.sum()) if len(sig) else 0,
                            rows=[dict(sym=r.sym, decision_bar_open_utc=r.decision_bar_open_utc, side=r.side, eligible=bool(r.eligible))
                                  for r in sig.itertuples()]))
    json.dump(out, open(os.path.join(a.out, 'lead_forward.json'), 'w', encoding='utf-8'), indent=1, default=str)
    print('LEAD', a.case, out['primary']['stats'], 'open', len(out['primary']['open']), 'signals', out['signals']['n'], flush=True)


# ------------------------------------------------------------------ parity with earlier rows
def parity(prev, cur):
    """Every closed trade and every signal of an earlier row must be present, identical, in the new row. Returns a list of problems."""
    probs = []
    for k, p in prev.get('leads', {}).items():
        c = cur['leads'].get(k)
        if c is None or 'primary' not in c or 'primary' not in p: continue
        pc, cc = p['primary']['closed'], c['primary']['closed']
        key = lambda r: (r['sym'], r['t_in'])
        cm = {key(r): r for r in cc}
        for r in pc:
            if cm.get(key(r)) != r: probs.append(f'{k}: closed trade {key(r)} of the earlier row is missing or differs')
        cs = {(r['sym'], r['decision_bar_open_utc'], r['side']): r for r in c['signals']['rows']}
        for r in p['signals']['rows']:
            if cs.get((r['sym'], r['decision_bar_open_utc'], r['side'])) != r: probs.append(f'{k}: signal {r["sym"]} {r["decision_bar_open_utc"]} missing or differs')
    return probs


def latest_row(rows_root, B):
    if not os.path.isdir(rows_root): return None
    """The latest earlier COMPLETE row (INCOMPLETE / NONPARITY rows are never a parity reference)."""
    for k in sorted((int(x) for x in os.listdir(rows_root) if x.isdigit() and int(x) < B), reverse=True):
        p = os.path.join(rows_root, str(k), 'row.json')
        if os.path.exists(p):
            r = json.load(open(p, encoding='utf-8'))
            if r.get('status') == 'COMPLETE': return r
    return None


def summary_md(row):
    L = [f"# LEADFWD-01 row {row['boundary_utc']} UTC: {row['status']}", '',
         f"Forward start {row['t_fwd_utc']} UTC. Research only, hypothetical bar replay (not fills). $8 risk, max one position per book. "
         "Signals sit on the 00:00 UTC candle; modeled entries fill at the next bar open (04:00 UTC). s36/s40 are count-failed exploratory controls.", '',
         '| lead | closes | sum R15 | mean R15 | mean R23 | open | signals since start | 40-order median closes |', '|---|---|---|---|---|---|---|---|']
    for k, v in row['leads'].items():
        if 'primary' not in v: L.append(f'| {k} | {v.get("status")} | | | | | | |'); continue
        s = v['primary']['stats']
        tag = ' (control)' if 'CONTROL' in v.get('label', '') else ''
        L.append(f"| {k}{tag} | {s['n']} | {s.get('sumR15', 0)} | {s.get('meanR15', '-')} | {s.get('meanR23', '-')} | {len(v['primary']['open'])} | "
                 f"{v['signals']['n']} | {v['orders']['median_closes']} |")
    p = row.get('pooled') or {}
    L += ['', f"Pooled diagnostic (six books together, never a pass or decision): {p.get('n', 0)} closes, sum R15 {p.get('sumR15', 0)}."]
    if row.get('flags'): L += ['', 'Flags: ' + '; '.join(row['flags'])]
    if row.get('problems'): L += ['', 'Problems: ' + '; '.join(row['problems'])]
    cut = [s for s, i in row['coverage'].items() if i['status'] != 'OK']
    L += ['', f"Coverage: {len(row['coverage']) - len(cut)} of {len(row['coverage'])} series complete to the boundary" + (f"; cut: {', '.join(cut)}" if cut else '') + '.']
    return '\n'.join(L) + '\n'


def review_flags(fz, leads):
    flags = []
    rv = fz['review']
    for k, v in leads.items():
        s = (v.get('primary') or {}).get('stats') or {}
        n = s.get('n', 0)
        if n >= 30:
            flags.append(f"{k}: {'FORWARD_CONSISTENT' if s['meanR15'] > 0 and s['meanR23'] >= 0 else 'FORWARD_KILLED'} at {n} closes")
        elif n >= 10 and s['meanR15'] <= -0.30:
            flags.append(f'{k}: EARLY_WARNING mean R15 {s["meanR15"]} at {n} closes')
    return flags


# ------------------------------------------------------------------ parent
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('fwd', nargs='?'); ap.add_argument('rows', nargs='?')
    ap.add_argument('--boundary-ms', type=int); ap.add_argument('--fresh'); ap.add_argument('--extra'); ap.add_argument('--orders', type=int, default=40)
    ap.add_argument('--worker'); ap.add_argument('--data'); ap.add_argument('--out'); ap.add_argument('--case'); ap.add_argument('--universe')
    ap.add_argument('--t-fwd')
    ap.add_argument('--leads', help='test only: subset of leads (a row with a subset is never written under rows/)')
    ap.add_argument('--skip-base-verify', action='store_true', help='test only: never used for a real row (the row is marked TEST)')
    a = ap.parse_args()
    if a.worker: return worker(a)
    fz = load_freeze()
    import run_screen as RS
    B = a.boundary_ms
    if B is None or B % 86400000: raise SystemExit('--boundary-ms must be a 00:00 UTC boundary')
    test = bool(a.leads or a.skip_base_verify or a.t_fwd)
    pre = B <= fz['t_fwd_ms']
    rows_root = os.path.join(a.rows, 'test' if test else ('dryrun' if pre else ''))
    rdir = os.path.join(rows_root, str(B))
    if os.path.exists(os.path.join(rdir, 'row.json')): raise SystemExit(f'{rdir} exists; rows are never rewritten')
    coins = ['BTCUSDT'] + sorted(fz['universe']['fresh134'] + fz['universe']['extra31'])
    t_start = time.time()
    if a.skip_base_verify:
        base_paths = {s: (os.path.join(a.fresh, f'{s}_1h.csv')) for s in coins if os.path.exists(os.path.join(a.fresh, f'{s}_1h.csv'))}
    else:
        base_paths = verify_base(fz, a.fresh or fz['base_history']['fresh_dir'], a.extra or fz['base_history']['extra_dir'], coins)
    fdir = os.path.join(a.fwd, str(B))
    fetch_sha, fwd_paths = verify_fetch(fz, fdir, B, sorted(base_paths))
    work = os.path.join(a.rows, '_work', str(B))
    if os.path.exists(work): shutil.rmtree(work)
    cov, problems = build_data(base_paths, fwd_paths, B, os.path.join(work, 'data'))
    universe = [s for s in sorted(base_paths) if s != 'BTCUSDT' and os.path.exists(os.path.join(work, 'data', f'{s}_1h.csv'))]
    leads = {}
    if not any(p.startswith('BTCUSDT') or 'overlap mismatch' in p for p in problems):   # a cut coin: leads still run, the row stays INCOMPLETE
        for k in (a.leads.split(',') if a.leads else list(fz['leads'])):
            L = fz['leads'][k]
            RS.CONTRACT['sources'][L['source']] = dict(commit=L['commit'], modules=L['modules'])
            out = os.path.join(work, k); os.makedirs(out, exist_ok=True)
            src = RS.export(L['source'], out)
            cmd = [sys.executable, os.path.abspath(__file__), '--worker', src, '--data', os.path.join(work, 'data'), '--out', out,
                   '--case', k, '--orders', str(a.orders), '--universe', ','.join(universe)]
            if a.t_fwd: cmd += ['--t-fwd', a.t_fwd]
            r = subprocess.run(cmd)
            if r.returncode: leads[k] = dict(status='ERROR', returncode=r.returncode); problems.append(f'{k}: worker failed'); continue
            leads[k] = json.load(open(os.path.join(out, 'lead_forward.json'), encoding='utf-8'))
    pooled = [r for v in leads.values() for r in (v.get('primary') or {}).get('closed', [])]
    row = dict(id='LEADFWD-01', boundary_ms=B, boundary_utc=time.strftime('%Y-%m-%d %H:%M', time.gmtime(B / 1000)),
               t_fwd_utc=a.t_fwd or fz['t_fwd_utc'], kind='TEST' if test else ('DRYRUN_BEFORE_START' if pre else 'FORWARD'),
               freeze_sha256=sha(FREEZE), runner_sha256=sha(os.path.abspath(__file__)), fetch_manifest_sha256=fetch_sha,
               coverage=cov, leads=leads,
               pooled=dict(label='DIAGNOSTIC ONLY: the six books pooled, never a pass or decision', n=len(pooled), sumR15=round(sum(r['R15'] for r in pooled), 4)),
               problems=problems, runtime_s=round(time.time() - t_start, 1))
    prev = latest_row(rows_root, B)
    if prev is not None and leads:
        problems += parity(prev, row); row['parity_with_boundary_ms'] = prev['boundary_ms']
    row['flags'] = review_flags(fz, leads)
    row['status'] = ('NONPARITY' if any('earlier row' in p or 'missing or differs' in p for p in problems)
                     else ('INCOMPLETE' if problems else 'COMPLETE'))
    os.makedirs(rdir, exist_ok=True)
    tmp = os.path.join(rdir, 'row.json.part')
    json.dump(row, open(tmp, 'w', encoding='utf-8'), indent=1, default=str)
    open(os.path.join(rdir, 'summary.md'), 'w', encoding='utf-8').write(summary_md(row))
    os.replace(tmp, os.path.join(rdir, 'row.json'))
    json.dump({'row.json': sha(os.path.join(rdir, 'row.json')), 'summary.md': sha(os.path.join(rdir, 'summary.md'))},
              open(os.path.join(rdir, 'receipts.json'), 'w', encoding='utf-8'), indent=1)
    print('ROW', B, row['status'], row['kind'], 'pooled', row['pooled'], 'problems', len(problems), flush=True)


if __name__ == '__main__':
    main()
