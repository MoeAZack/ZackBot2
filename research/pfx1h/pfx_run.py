"""PFX-1H economic runner (P3_P4_FROZEN_RULING @458011c1, P4). Research only; no account/keys/workers/schedulers.
Usage: python -I pfx_run.py <DATA root holding SPOTFLOW-D01-20261112/ (harness) and h5_wide_d01_20261112/> <premium inputs dir> <out> [--verify-only]"""
import hashlib, json, pathlib, sys, zipfile
import numpy as np, pandas as pd
P = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(P))
from pfx_signals import signal
H = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
DISC = (pd.Timestamp('2024-10-28 20:00'), pd.Timestamp('2024-10-28 22:00'))   # known canonical/native discrepancy window


def verify(root, prem):
    F = json.loads((P / 'freeze.json').read_text(encoding='utf-8'))
    if H(P / 'freeze.json') != (P / 'freeze.sha256').read_text(encoding='utf-8').strip(): raise SystemExit('freeze changed')
    for rel, h in F['files'].items():
        p = (P / rel[5:]) if rel.startswith('SELF/') else (prem / rel[8:]) if rel.startswith('PREMIUM/') else (root / rel)
        if H(p) != h: raise SystemExit('freeze mismatch ' + rel)
    return F


def load_premium(prem, s):
    rows = []
    for z in sorted((prem / s).glob(f'{s}-1h-*.zip')):
        zz = zipfile.ZipFile(z)
        for line in zz.read(zz.namelist()[0]).decode().splitlines():
            p = line.split(',')
            if not p[0].strip().isdigit(): continue
            ot, ct = int(p[0]), int(p[6])
            if ot > 10**14: ot, ct = ot // 1000, ct // 1000
            if ct - ot != 3599999 or ot % 3600000: raise SystemExit(f'premium endpoint {s} {ot}')
            rows.append((ot, float(p[1]), float(p[2]), float(p[3]), float(p[4])))
    d = pd.DataFrame(rows, columns=['t', 'o', 'h', 'l', 'c']); d.t = pd.to_datetime(d.t, unit='ms')
    if not (d.t.is_unique and d.t.is_monotonic_increasing): raise SystemExit(f'premium order {s}')
    return d.set_index('t')


def main(root, prem, out, verify_only=False):
    root, prem, out = pathlib.Path(root), pathlib.Path(prem), pathlib.Path(out); F = verify(root, prem)
    if verify_only: print('VERIFIED', len(F['files'])); return
    S = root / 'SPOTFLOW-D01-20261112'; C = root / 'h5_wide_d01_20261112' / 'clean'
    sys.path.insert(0, str(S / 'harness/research/rsr1h'))
    import run_rsr1h as R
    X, D = R.X, R.D
    T0 = pd.Timestamp('2024-10-11 17:00'); T1 = pd.Timestamp('2026-09-27 23:00')
    X.T0 = D.T0 = T0; X.T1 = D.T1 = T1; X.SPLIT = D.SPLIT = pd.Timestamp('2025-10-01')
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
        pm = load_premium(prem, s)
        grid = pd.date_range(min(f.index[0], pm.index[0]), T1, freq='h')
        fg, pg = f.reindex(grid), pm.reindex(grid)
        atr = pd.Series(a['atr'], index=pd.DatetimeIndex(a['t'])).reindex(grid)
        side, dg = signal(pg, fg, atr)
        pos = {pd.Timestamp(t): k for k, t in enumerate(a['t'])}
        elig = pd.Series(a['elig'], index=pd.DatetimeIndex(a['t'])).reindex(grid, fill_value=False)
        dec = []
        for t, sd in side.loc[T0:T1].items():
            if not sd: continue
            r = dict(sym=s, signal_open=str(t), hour_close=str(t + pd.Timedelta(hours=1)), scheduled_fill=str(t + pd.Timedelta(hours=1)), side=int(sd),
                     prem_low=pg.l[t], prem_high=pg.h[t], mL=dg['mL'][t], madL=dg['madL'][t], mH=dg['mH'][t], madH=dg['madH'][t],
                     disp=fg.c[t] - fg.o[t], atr=atr[t], discrepancy_window=bool(DISC[0] <= t < DISC[1] or DISC[0] <= t + pd.Timedelta(hours=1) < DISC[1]))
            k = pos.get(t)
            if k is None or not elig[t]: raw.append(dict(r, refused='ineligible')); continue
            if k + 1 >= len(a['t']) or a['t'][k + 1] - a['t'][k] != np.timedelta64(1, 'h'): raw.append(dict(r, refused='missing_next_hour')); continue
            dist = 3 * float(a['atr'][k])
            dec.append((a['t'][k + 1], int(sd), k, dist)); raw.append(dict(r, dist=dist))
        coins[s] = {'a': a, 'dec': dec}; counts[s] = {'long': sum(x[1] > 0 for x in dec), 'short': sum(x[1] < 0 for x in dec)}
        masks.append(elig.loc[T0:T1].to_numpy())
    out.mkdir(parents=True, exist_ok=True); pd.DataFrame(raw).to_csv(out / 'raw_signals.csv', index=False)
    run_all(R, X, coins, syms, counts, masks, T0, T1, out, dict(id='PFX-1H', freeze_sha256=H(P / 'freeze.json')))


def decomp(tr):
    cl = [t for t in tr if t['why'] != 'TERMINAL_OPEN']
    if not cl: return None
    g = lambda k: float(np.mean([t[k] for t in cl])) / 8.0
    return dict(gross_after_slip_R=g('gross'), slip_R=g('slip'), gross_before_slip_R=g('gross') + g('slip'), fees_R=g('fees'), funding_R=g('fund'), net_R=g('pnl'))


def run_all(R, X, coins, syms, counts, masks, T0, T1, out, head):
    orders = [sorted(syms)] + [list(np.random.default_rng(20261112 + k).permutation(sorted(syms))) for k in range(1, 40)]
    weeks = (T1 - T0).total_seconds() / 604800; ew = np.any(masks, axis=0).sum() / 168
    res = dict(head, signal_counts=counts, raw_decisions=sum(len(c['dec']) for c in coins.values()), orders=orders, paths={},
               limitations='consumed DEV, survivor universe, premium-extreme proxy (not liquidation data), funding stress proxy, current rules, no live parity, official archive lacks 2026-06-29 premium hours')
    for mode in ('causal', 'worst'):
        res['paths'][mode] = {}
        for cb in (7, 15, 23):
            rr = []
            for k, order in enumerate(orders):
                tr, sk = R.simulate(coins, syms, order, cb, mode)
                if any(t['t_in'] > T1 or t['t_out'] > T1 + pd.Timedelta(hours=1) for t in tr): raise SystemExit('cutoff')
                sm = X.summarize(tr, sk, weeks, ew); sm['order'] = k
                sm['admitted'] = len(tr); sm['explicit_refusals'] = len(sk)
                sm['implied_capacity_refusals'] = res['raw_decisions'] - len(tr) - len(sk)
                cl = [t for t in tr if t['why'] != 'TERMINAL_OPEN']
                sm['by_coin_meanR'] = {s: float(np.mean([t['R_budget'] for t in cl if t['sym'] == s])) for s in sorted({t['sym'] for t in cl})}
                sm['terminal_open'] = len(tr) - len(cl)
                sm['lots_touching_discrepancy'] = sum(1 for t in tr if t['t_in'] <= pd.Timestamp('2024-10-28 22:00') and t['t_out'] >= pd.Timestamp('2024-10-28 20:00'))
                if k == 0: sm['decomp_order0'] = decomp(tr)
                rr.append(sm)
                if k == 0: pd.DataFrame([{kk: v for kk, v in t.items() if kk not in ('marks', 'cash')} for t in tr]).to_csv(out / f'{mode}.{cb}bp.order0.trades.csv', index=False)
            df = pd.DataFrame(rr)
            def q(col):
                if col not in df or not df[col].notna().any(): return None
                a = df[col]; return {'median': float(a.median()), 'p10': float(a.quantile(.1)), 'p90': float(a.quantile(.9)), 'min': float(a.min()), 'max': float(a.max())}
            cols = ('closes', 'closes_per_cal_week', 'closes_per_elig_week', 'meanR_budget', 'meanR_actual', 'early_meanR', 'later_meanR', 'exbest_meanR', 'closedR_maxdd', 'marked_dd_R', 'longest_underwater_days')
            res['paths'][mode][f'{cb}bp'] = {'order0': rr[0], 'across_orders': {c: q(c) for c in cols}, 'all_orders': rr}
            (out / 'results.json').write_text(json.dumps(res, indent=2, default=str), encoding='utf-8'); print(mode, cb, flush=True)
    res['gates'] = X.gates(res['paths']['causal'], (res['paths']['worst']['15bp']['across_orders']['meanR_budget'] or {}).get('median'))
    res['gates']['median_3_closes_per_week'] = (res['paths']['causal']['15bp']['across_orders']['closes_per_cal_week'] or {}).get('median', 0) >= 3
    res['gates']['PASS'] = all(v for k, v in res['gates'].items() if k != 'PASS')
    (out / 'results.json').write_text(json.dumps(res, indent=2, default=str), encoding='utf-8'); print('COMPLETE', res['gates']['PASS'], flush=True)


if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    main(a[0], a[1], a[2], '--verify-only' in sys.argv)
