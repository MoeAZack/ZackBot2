"""IVS-ECON-D01: 12-case DEPENDENT economics diagnostic of the UNCHANGED IVS-1H rule (contract 0547b96f).
Original IVS-1H stays FREQUENCY_FAIL (544a33d2). This is a post-signal-count / pre-P&L scope choice, not preregistered before counts.
Mechanism (no IVS source edits): run the byte-pinned original research/ivs1h/ivs_run.main (which verifies the original freeze
ef2c4cc1, all 32 pins) with two in-memory hooks: (1) its Stage-B gate result is recorded but not allowed to stop the build;
(2) the `pfx_run` module it imports is replaced by a capture stub, so the built coin decisions come back here instead of the
40-order grid. Then exactly 12 cases = {order0 alphabetical PRIMARY, fixed cohort DIAGNOSTIC} x {7,15,23} bp x {causal, 4h stop-first}
through the unchanged shared RSR/X2 lot simulator (R.simulate). No 40-order grid, no p10, no LEAD_PASS.
Usage: python -I econ_run.py <DATA root> <repo root> <out> [--identity-only]"""
import hashlib, json, pathlib, sys, types
import numpy as np, pandas as pd
P = pathlib.Path(__file__).resolve().parent
H = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
COHORT = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT', 'DOGEUSDT', 'BNBUSDT', 'ADAUSDT', 'LINKUSDT', 'AVAXUSDT']
TUPLE = ['sym', 'signal_open', 'scheduled_fill', 'side']


def verify_self():
    F = json.loads((P / 'freeze.json').read_text(encoding='utf-8'))
    if H(P / 'freeze.json') != (P / 'freeze.sha256').read_text(encoding='utf-8').strip(): raise SystemExit('freeze changed')
    repo = P.parents[1]
    for rel, h in F['files'].items():
        if H(repo / rel) != h: raise SystemExit('freeze mismatch ' + rel)
    return F


def build(data, repo, out):
    """Run the ORIGINAL ivs_run.main unchanged; capture (R, X, coins, syms, counts, masks, T0, T1) and its Stage-B record."""
    sys.path.insert(0, str(repo / 'research/ivs1h'))
    import ivs_run
    cap = {}
    orig_fb = ivs_run.frequency_bound
    def fb_hook(fills, T0, T1):
        r = orig_fb(fills, T0, T1); cap['stage_b_original'] = dict(r); return dict(r, FREQUENCY_FAIL=False)
    ivs_run.frequency_bound = fb_hook
    stub = types.ModuleType('pfx_run')
    def run_all(R, X, coins, syms, counts, masks, T0, T1, o, head): cap.update(R=R, X=X, coins=coins, syms=syms, counts=counts, masks=masks, T0=T0, T1=T1, head=head)
    stub.run_all = run_all; sys.modules['pfx_run'] = stub
    ivs_run.main(str(data), str(repo / 'docs/research/ivs1h/inputs/btcbvol_hourly.csv'), str(out / 'rebuild'))
    if 'coins' not in cap: raise SystemExit('capture failed')
    return cap


def identity(out, repo, cap):
    a = pd.read_csv(repo / 'docs/research/ivs1h/results/raw_signals.csv'); b = pd.read_csv(out / 'rebuild/raw_signals.csv')
    ka = sorted(map(tuple, a[TUPLE].astype(str).to_numpy())); kb = sorted(map(tuple, b[TUPLE].astype(str).to_numpy()))
    dec = sorted((s, str(pd.Timestamp(d[0])), int(d[1])) for s, c in cap['coins'].items() for d in c['dec'])
    exp = sorted((r[0], r[2], int(r[3])) for r in ka)
    fills = {d[1] for d in dec}
    ok = ka == kb and len(ka) == 826 and dec == exp and len(fills) == 153 and cap['stage_b_original']['FREQUENCY_FAIL']
    res = dict(original_rows=len(ka), rebuilt_rows=len(kb), decisions=len(dec), distinct_fill_times=len(fills),
               byte_identical_raw_signals=H(repo / 'docs/research/ivs1h/results/raw_signals.csv') == H(out / 'rebuild/raw_signals.csv'),
               original_stage_b=cap['stage_b_original'], PARITY=bool(ok))
    if not ok: raise SystemExit('NONPARITY ' + json.dumps(res, default=str))
    return res


def case(cap, order, cb, mode):
    R, X = cap['R'], cap['X']; T0, T1 = cap['T0'], cap['T1']
    weeks = (T1 - T0).total_seconds() / 604800; ew = np.any(cap['masks'], axis=0).sum() / 168
    tr, sk = R.simulate(cap['coins'], cap['syms'], order, cb, mode)
    if any(t['t_in'] > T1 or t['t_out'] > T1 + pd.Timedelta(hours=1) for t in tr): raise SystemExit('cutoff')
    sm = X.summarize(tr, sk, weeks, ew)
    raw = sum(len(c['dec']) for c in cap['coins'].values())
    cl = [t for t in tr if t['why'] != 'TERMINAL_OPEN']
    for t in tr:   # simple cash identity on existing simulator output
        if abs(t['pnl'] - (t['gross'] - t['fees'] - t['fund'])) > 1e-9: raise SystemExit('cash identity')
    Rb = np.array([t['R_budget'] for t in cl]); pnl = np.array([t['pnl'] for t in cl])
    g = lambda k: float(np.mean([t[k] for t in cl])) / 8.0 if cl else None
    grp = lambda key: {str(k): dict(n=int(len(v)), sumR=float(v.sum()), meanR=float(v.mean())) for k, v in pd.Series(Rb, index=[key(t) for t in cl]).groupby(level=0)} if cl else {}
    pos = pnl[pnl > 0].sum(); neg = -pnl[pnl < 0].sum()
    coin_sum = pd.Series(Rb, index=[t['sym'] for t in cl]).groupby(level=0).sum() if cl else pd.Series(dtype=float)
    sm.update(order_name=None, cost_bp=cb, mode=mode, raw_decisions=raw, admitted=len(tr), explicit_refusals=len(sk),
              residual_capacity_refusals_labelled=raw - len(tr) - len(sk), actual_fills_per_cal_week=len(tr) / weeks,
              completed_closes=len(cl), terminal_open=len(tr) - len(cl),
              admitted_ids=[f"{t['sym']}|{t['t_sig']}|{t['side']}" for t in tr],
              closed_pnl_usd=float(pnl.sum()), profit_factor=float(pos / neg) if neg > 0 else None,
              decomp_R=dict(gross_before_slip=g('gross') + g('slip'), slip=g('slip'), gross_after_slip=g('gross'), fees=g('fees'), funding=g('fund'), net=g('pnl')) if cl else None,
              by_coin_R=grp(lambda t: t['sym']), by_side_R=grp(lambda t: 'long' if t['side'] > 0 else 'short'),
              by_half_R=grp(lambda t: 'early' if t['t_in'] < pd.Timestamp('2025-10-01') else 'later'),
              by_quarter_R=grp(lambda t: f"{t['t_in'].year}Q{(t['t_in'].month - 1) // 3 + 1}"),
              best_trade_R=float(Rb.max()) if cl else None, top_coin_share_of_abs_R=float(coin_sum.abs().max() / coin_sum.abs().sum()) if len(coin_sum) and coin_sum.abs().sum() > 0 else None,
              lots_touching_discrepancy=sum(1 for t in tr if t['t_in'] <= pd.Timestamp('2024-10-28 22:00') and t['t_out'] >= pd.Timestamp('2024-10-28 20:00')))
    return sm, tr


def main(data, repo, out, identity_only=False):
    data, repo, out = pathlib.Path(data), pathlib.Path(repo), pathlib.Path(out); F = verify_self()
    out.mkdir(parents=True, exist_ok=True)
    cap = build(data, repo, out); idn = identity(out, repo, cap)
    res = dict(id='IVS-ECON-D01', contract='0547b96f', freeze_sha256=H(P / 'freeze.json'), identity=idn,
               scope='DEPENDENT post-signal-count/pre-P&L diagnostic; original IVS-1H remains FREQUENCY_FAIL; no p10/40-order/LEAD_PASS', cases={})
    if identity_only:
        (out / 'identity.json').write_text(json.dumps(res, indent=2, default=str), encoding='utf-8'); print('PARITY', json.dumps(idn, default=str)); return
    orders = dict(order0_alphabetical_PRIMARY=sorted(cap['syms']), cohort_DIAGNOSTIC=COHORT)
    assert sorted(COHORT) == sorted(cap['syms'])
    for on, order in orders.items():
        for mode in ('causal', 'worst'):
            for cb in (7, 15, 23):
                sm, tr = case(cap, order, cb, mode); sm['order_name'] = on
                res['cases'][f'{on}.{mode}.{cb}bp'] = sm
                pd.DataFrame([{k: v for k, v in t.items() if k not in ('marks', 'cash')} for t in tr]).to_csv(out / f'{on}.{mode}.{cb}bp.trades.csv', index=False)
                print(on, mode, cb, sm['completed_closes'], round(sm['meanR_budget'], 4) if sm.get('meanR_budget') is not None else None, flush=True)
    assert len(res['cases']) == 12
    (out / 'results.json').write_text(json.dumps(res, indent=2, default=str), encoding='utf-8'); print('COMPLETE')


if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    main(a[0], a[1], a[2], '--identity-only' in sys.argv)
