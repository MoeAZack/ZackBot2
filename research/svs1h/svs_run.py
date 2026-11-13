"""SVS-1H economic runner (frozen ruling 1a1effaa P1). Research only; no account/keys/workers/schedulers.
Usage: python -I svs_run.py <DATA root holding SPOTFLOW-D01-20261112/ and h5_wide_d01_20261112/> <out dir> [--verify-only]
Execution, costs, orders, paths and gates reuse the reviewed RSR/X2 harness exactly as SPB-D01 spb_run.py did."""
import csv, hashlib, io, json, pathlib, sys, zipfile
import numpy as np, pandas as pd
P = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(P))
from spb_signals_6f16d49a import spot_row, decision
from svs_signals import signal
H = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def verify(root):
    F = json.loads((P / 'freeze.json').read_text(encoding='utf-8'))
    if H(P / 'freeze.json') != (P / 'freeze.sha256').read_text(encoding='utf-8').strip(): raise SystemExit('freeze changed')
    for rel, h in F['files'].items():
        p = (P / rel[5:]) if rel.startswith('SELF/') else (root / rel)
        if H(p) != h: raise SystemExit('freeze mismatch ' + rel)
    return F


def main(root, out, verify_only=False):
    root = pathlib.Path(root); out = pathlib.Path(out); F = verify(root)
    if verify_only: print('VERIFIED', len(F['files'])); return
    S = root / 'SPOTFLOW-D01-20261112'; C = root / 'h5_wide_d01_20261112' / 'clean'
    M = json.loads((S / 'data_manifest_frozen.json').read_text(encoding='utf-8'))
    sys.path.insert(0, str(S / 'harness/research/rsr1h'))
    import run_rsr1h as R
    X, D = R.X, R.D
    T0 = pd.Timestamp('2024-10-11 17:00'); T1 = pd.Timestamp('2026-09-27 23:00')
    X.T0 = D.T0 = T0; X.T1 = D.T1 = T1; X.SPLIT = D.SPLIT = pd.Timestamp('2025-10-01')
    syms = M['symbols']; assert syms == sorted(F['coins']) == F['coins']
    rules = json.loads((S / 'harness/data/exchange_rules_testnet.json').read_text(encoding='utf-8'))['symbols']
    coins, raw, counts, masks = {}, [], {}, []
    for s in syms:
        a = D.load(str(C), s, rules)
        n = len(a['t']); mask = a['t'] <= np.datetime64(T1)
        for k, v in list(a.items()):
            if isinstance(v, np.ndarray) and len(v) == n: a[k] = v[mask]
        f = pd.read_csv(C / f'{s}_1h.csv', usecols=['t', 'o', 'h', 'l', 'c', 'qv']); f.t = pd.to_datetime(f.t, unit='ms')
        f = f.set_index('t').asfreq('h').loc[:T1]
        atr = pd.Series(a['atr'], index=pd.DatetimeIndex(a['t'])).reindex(f.index)
        rows = []
        for m in M['months']:
            z = zipfile.ZipFile(S / f'{s}-1h-{m}.zip'); factor = 1 if m < '2025-01' else 1000
            with z.open(z.namelist()[0]) as zf:
                rows.extend(spot_row(r, factor) for r in csv.reader(io.TextIOWrapper(zf, encoding='utf8')))
        sp = pd.DataFrame(rows, columns=['t', 'c', 'qv']); sp.t = pd.to_datetime(sp.t, unit='ms'); sp = sp.set_index('t').asfreq('h')
        sg, z, sh, Q = signal(sp, f, atr); dec = []
        for t, sd in sg.loc[T0:T1].items():
            if not sd: continue
            d = decision(a, t, sd)
            if d is None: raw.append(dict(sym=s, signal_open=str(t), side=int(sd), z=z[t], share=sh[t], Q=Q[t], refused='missing_lag_or_atr')); continue
            dec.append(d)
            raw.append(dict(sym=s, signal_open=str(t), decision_available=str(t + pd.Timedelta(hours=1)), scheduled_entry=str(pd.Timestamp(d[0])),
                            side=int(sd), kind=('continue' if (z[t] >= 2) else 'fade'), z=z[t], share=sh[t], Q=Q[t], dist=d[3]))
        coins[s] = {'a': a, 'dec': dec}; counts[s] = {'long': sum(x[1] > 0 for x in dec), 'short': sum(x[1] < 0 for x in dec)}
        masks.append(pd.Series(a['elig'], index=pd.DatetimeIndex(a['t'])).reindex(pd.date_range(T0, T1, freq='h'), fill_value=False).to_numpy())
    out.mkdir(parents=True, exist_ok=True); pd.DataFrame(raw).to_csv(out / 'raw_signals.csv', index=False)
    run_all(R, X, coins, syms, raw, counts, masks, T0, T1, out, dict(id='SVS-1H', freeze_sha256=H(P / 'freeze.json')))


def run_all(R, X, coins, syms, raw, counts, masks, T0, T1, out, head):
    orders = [sorted(syms)] + [list(np.random.default_rng(20261112 + k).permutation(sorted(syms))) for k in range(1, 40)]
    weeks = (T1 - T0).total_seconds() / 604800; ew = np.any(masks, axis=0).sum() / 168
    res = dict(head, signal_counts=counts, raw_decisions=sum(len(c['dec']) for c in coins.values()), orders=orders, paths={},
               limitations='consumed DEV, fixed survivor universe, asynchronous hourly venues, funding stress proxy (no native funding), current rules, no live parity')
    for mode in ('causal', 'worst'):
        res['paths'][mode] = {}
        for cb in (7, 15, 23):
            rr = []
            for k, order in enumerate(orders):
                tr, sk = R.simulate(coins, syms, order, cb, mode)
                if any(t['t_in'] > T1 or t['t_out'] > T1 + pd.Timedelta(hours=1) for t in tr): raise SystemExit('cutoff')
                sm = X.summarize(tr, sk, weeks, ew); sm['order'] = k
                sm['admitted'] = len(tr); sm['explicit_refusals'] = len(sk)
                sm['capacity_refusals'] = res['raw_decisions'] - len(tr) - len(sk)
                sm['by_coin_meanR'] = {s: float(np.mean([t['R_budget'] for t in tr if t['sym'] == s and t['why'] != 'TERMINAL_OPEN']))
                                       for s in sorted({t['sym'] for t in tr if t['why'] != 'TERMINAL_OPEN'})}
                sm['terminal_open'] = sum(t['why'] == 'TERMINAL_OPEN' for t in tr)
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
    main(a[0], a[1], '--verify-only' in sys.argv)
