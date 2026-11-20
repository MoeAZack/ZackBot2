"""IVS-1H runner (P5_IVS_FROZEN_RULING @82f70688). Research only; no account/keys/workers/schedulers/collectors.
Stage B (frequency upper bound) always runs first; Stage C economics runs only if Stage B does not FREQUENCY_FAIL.
Execution/economics reuse the frozen PFX runner (run_all/decomp -> shared RSR/X2 simulator), unchanged.
Usage: python -I ivs_run.py <DATA root holding SPOTFLOW-D01-20261112/ and h5_wide_d01_20261112/> <hourly table csv> <out> [--verify-only]"""
import hashlib, json, pathlib, sys
import numpy as np, pandas as pd
P = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(P)); sys.path.insert(0, str(P.parent / 'pfx1h'))
from ivs_signals import levels, zscores, events, coin_sides, frequency_bound, decide
H = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
DISC = (pd.Timestamp('2024-10-28 20:00'), pd.Timestamp('2024-10-28 22:00'))
T0 = pd.Timestamp('2024-10-11 17:00'); T1 = pd.Timestamp('2026-09-27 23:00')


def verify(root, table):
    F = json.loads((P / 'freeze.json').read_text(encoding='utf-8'))
    if H(P / 'freeze.json') != (P / 'freeze.sha256').read_text(encoding='utf-8').strip(): raise SystemExit('freeze changed')
    for rel, h in F['files'].items():
        p = ((P / rel[5:]) if rel.startswith('SELF/') else (P.parent / rel[6:]) if rel.startswith('REPOR/')
             else (P.parents[1] / 'docs/research/ivs1h' / rel[5:]) if rel.startswith('DOCS/') else table if rel == 'HOURLY' else root / rel)
        if H(p) != h: raise SystemExit('freeze mismatch ' + rel)
    return F


def main(root, table, out, verify_only=False):
    root, table, out = pathlib.Path(root), pathlib.Path(table), pathlib.Path(out); F = verify(root, table)
    if verify_only: print('VERIFIED', len(F['files'])); return
    S = root / 'SPOTFLOW-D01-20261112'; C = root / 'h5_wide_d01_20261112' / 'clean'
    sys.path.insert(0, str(S / 'harness/research/rsr1h'))
    import run_rsr1h as R
    import pfx_run
    X, D = R.X, R.D
    X.T0 = D.T0 = T0; X.T1 = D.T1 = T1; X.SPLIT = D.SPLIT = pd.Timestamp('2025-10-01')
    hourly = pd.read_csv(table, dtype=str, keep_default_na=False)
    V = levels(hourly); x, z = zscores(V); ev_tab = events(z)
    syms = list(F['coins'])
    rules = json.loads((S / 'harness/data/exchange_rules_testnet.json').read_text(encoding='utf-8'))['symbols']
    coins, raw, counts, masks = {}, [], {}, []
    for s in syms:
        a = D.load(str(C), s, rules)
        n = len(a['t']); mask = a['t'] <= np.datetime64(T1)
        for k, v in list(a.items()):
            if isinstance(v, np.ndarray) and len(v) == n: a[k] = v[mask]
        f = pd.read_csv(C / f'{s}_1h.csv', usecols=['t', 'o', 'h', 'l', 'c']); f.t = pd.to_datetime(f.t, unit='ms')
        f = f.set_index('t').loc[:T1]
        grid = pd.date_range(f.index[0], T1, freq='h'); fg = f.reindex(grid)
        atr = pd.Series(a['atr'], index=pd.DatetimeIndex(a['t'])).reindex(grid)
        ev = ev_tab.reindex(grid, fill_value=False).astype(bool)
        side = coin_sides(ev, fg, atr)
        pos = {pd.Timestamp(t): k for k, t in enumerate(a['t'])}
        elig = pd.Series(a['elig'], index=pd.DatetimeIndex(a['t'])).reindex(grid, fill_value=False)
        dec = []
        for t, sd in side.loc[T0:T1].items():
            if not sd: continue
            r = dict(sym=s, signal_open=str(t), hour_close=str(t + pd.Timedelta(hours=1)), scheduled_fill=str(t + pd.Timedelta(hours=1)), side=int(sd),
                     V=V.get(t), x=x.get(t), z=z.get(t), disp=fg.c[t] - fg.o[t], atr=atr[t],
                     discrepancy_window=bool(DISC[0] <= t < DISC[1] or DISC[0] <= t + pd.Timedelta(hours=1) < DISC[1]))
            k = pos.get(t)
            why, ok = decide(k, a['t'], a['atr'], bool(elig[t]))
            if why: raw.append(dict(r, refused=why)); continue
            dec.append((ok[0], int(sd), k, ok[1])); raw.append(dict(r, dist=ok[1]))
        coins[s] = {'a': a, 'dec': dec}; counts[s] = {'long': sum(x_[1] > 0 for x_ in dec), 'short': sum(x_[1] < 0 for x_ in dec)}
        masks.append(elig.loc[T0:T1].to_numpy())
    out.mkdir(parents=True, exist_ok=True); pd.DataFrame(raw).to_csv(out / 'raw_signals.csv', index=False)
    evw = ev_tab.loc[T0:T1]
    fills = [pd.Timestamp(d[0]) for c in coins.values() for d in c['dec']]
    fb = frequency_bound(fills, T0, T1)
    fb['distinct_fill_times_le_T1'] = len({t for t in fills if t <= T1})
    fb['distinct_raw_signal_times'] = len({r['signal_open'] for r in raw})
    fb['global_events_in_window'] = int(evw.sum())
    fb['z_defined_hours_in_window'] = int(z.loc[T0:T1].notna().sum())
    fb['valid_V_hours_in_window'] = int(V.loc[T0:T1].notna().sum())
    fb['window_hours'] = int(((T1 - T0) / pd.Timedelta(hours=1)) + 1)
    fb['raw_signals'] = len(raw); fb['eligible_decisions'] = sum(len(c['dec']) for c in coins.values())
    fb['refusals'] = {k: sum(1 for r in raw if r.get('refused') == k) for k in ('ineligible', 'missing_next_hour')}
    fb['per_coin_side'] = counts
    fb['by_month_distinct_fill_times'] = pd.Series(1, index=sorted(set(fills))).groupby(lambda t: t.strftime('%Y-%m')).sum().to_dict() if fills else {}
    head = dict(id='IVS-1H', freeze_sha256=H(P / 'freeze.json'), stage_b=fb)
    if fb['FREQUENCY_FAIL']:
        head.update(verdict='FREQUENCY_FAIL', economics='NOT_EVALUATED')
        (out / 'results.json').write_text(json.dumps(head, indent=2, default=str), encoding='utf-8'); print('FREQUENCY_FAIL', json.dumps(fb, default=str)); return
    pfx_run.run_all(R, X, coins, syms, counts, masks, T0, T1, out, head)


if __name__ == '__main__':
    a = [x_ for x_ in sys.argv[1:] if not x_.startswith('--')]
    main(a[0], a[1], a[2], '--verify-only' in sys.argv)
