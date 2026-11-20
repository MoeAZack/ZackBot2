"""RSR-1H: residual shock reversal on 1h bars, canonical 40 coins. RESEARCH ONLY; one frozen setting, no search.

Approved by Codex (PR #337 6830439673, with pre-freeze definitions 1-7) and moe. Contract: research/rsr1h/contract.json,
frozen with this runner and its tests before any RSR-1H count or outcome. Related to the broad reversal family (not claimed
statistically independent). Market-ADJUSTED signal; the book is NOT hedged / market-neutral. Nothing here touches the
engine, runtime, UI, keys or accounts.

Features (hour bar t = the 1h bar opening at t and closing at t + 1 h; everything uses closed bars only):
- r_i,t = ln(c_t / c_{t-1}) only when the bar at t-1 h exists (no forward fill); else missing.
- Contributors at t: coins D01-eligible at t (point-in-time, canonical rule) with a finite r. Leave-one-out market
  m_i,t = mean r over the OTHER contributors; needs >= 8 of them, else missing. The candidate's own r never enters m_i.
- alpha_i,t, beta_i,t: OLS with intercept of r_i,s on m_i,s over matched finite pairs, s in [t-168 h, t-1 h]; needs >= 120
  pairs and positive finite market variance, else missing. No clipping.
- e_i,t = r_i,t - alpha_i,t - beta_i,t * m_i,t (each innovation keeps the fit available at ITS own hour).
- sigma_i,t = population std (ddof 0) of e_i,s, s in [t-168 h, t-1 h], >= 120 finite; positive finite required.
- z_i,t = (e_{t-2} + e_{t-1} + e_t) / (sigma_i,t * sqrt 3), all three innovations finite (hence three consecutive valid hours).
  Long if z <= -3, short if z >= +3 (level thresholds). Skip if |m_{t-2} + m_{t-1} + m_t| >= 0.015 (log). Candidate must be
  D01-eligible at t. sqrt 3 is a fixed heuristic scale, not a calibrated significance claim.
Trade: decision at the signal bar close (t + 1 h); entry at the next 1h OPEN, which must exist exactly at t + 1 h (else
refused). dist = 3 x Wilder ATR14(1h) of the signal bar. Stop entry -/+ dist, target 2R, no partial, no breakeven, time exit
at the first management-bar close >= entry + 12 h. Adverse open beyond the stop (not in the first bar) -> exit at that open.
Execution: X2-WIDE harness (research/x2wide/run_x2wide.py a525ed69, imported unchanged) for sizing, filters, costs 7/15/23 bp,
funding proxy, max 1 global, 40 tie orders, Cairo halt, terminal MTM, summaries and gates; only run_lot is replaced (X2's
4h partial/BE/120 h management is NOT used). Paths: causal = 1h management bars; worst = UTC 4h bins, stop-first.
Arms: union40 (primary, gated) and the original9 / extra31 subsets (diagnostic; features always use all 40 coins).
Usage: python run_rsr1h.py <canonical_clean_dir> <out_dir>
"""
import sys, os, json, hashlib
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, 'research', 'x2wide'), HERE]
XSRC = os.path.join(ROOT, 'research', 'x2wide', 'run_x2wide.py')
assert hashlib.sha256(open(XSRC, 'rb').read()).hexdigest() == '8251f9b34d3eef1b1847ecce4141903df7de363155938407fb369168f27cfad7'
import run_x2wide as X
D = X.D

WIN, MIN_OBS, MIN_CONTRIB, Z_TH, MKT_SKIP = 168, 120, 8, 3.0, 0.015
STOP_ATR, TP_R, CAP_H = 3.0, 2.0, 12
ALL40 = list(D.ORIG) + list(D.EXTRA)
ARMS = dict(union40=ALL40, original9=list(D.ORIG), extra31=list(D.EXTRA))
HOUR, H4 = X.HOUR, X.H4
SLIP, FEES, FUND_H, RISK, CAP_NOTIONAL = X.SLIP, X.FEES, X.FUND_H, X.RISK, X.CAP_NOTIONAL


def panel(raw, syms):
    """Hourly grid panel: r (log return, consecutive hours only) and elig, as DataFrames [grid x syms]."""
    t0 = min(raw[s]['t'][0] for s in syms); t1 = max(raw[s]['t'][-1] for s in syms)
    grid = pd.date_range(t0, t1, freq='h')
    R, E = {}, {}
    for s in syms:
        a = raw[s]; t = pd.DatetimeIndex(a['t'])
        c = pd.Series(a['c'], index=t)
        prev_ok = np.r_[False, np.diff(a['t']) == HOUR]
        r = pd.Series(np.where(prev_ok, np.log(a['c'] / np.r_[np.nan, a['c'][:-1]]), np.nan), index=t)
        R[s] = r.reindex(grid); E[s] = pd.Series(a['elig'], index=t).reindex(grid).fillna(False).astype(bool)
    return pd.DataFrame(R), pd.DataFrame(E)


def loo_market(R, E):
    """m[i] at t = mean r of the OTHER eligible coins with finite r; NaN if fewer than MIN_CONTRIB others."""
    inc = E & R.notna()
    rz = R.where(inc, 0.0)
    tot, n = rz.sum(axis=1), inc.sum(axis=1)
    others = (-inc.astype(int)).add(n, axis=0)
    m = (-rz).add(tot, axis=0) / others.where(others > 0)
    return m.where(others >= MIN_CONTRIB)


def _roll_prior(x, w=WIN):
    """Sum over [t-w, t-1] (calendar rows on the hourly grid), NaN treated as 0; with matching counts."""
    return x.fillna(0.0).rolling(w, min_periods=1).sum().shift(1)


def features(R, M):
    """alpha, beta, e, sigma, z per coin (DataFrames on the grid). Prior-only, leave-one-out market."""
    ok = R.notna() & M.notna()
    x = M.where(ok); y = R.where(ok)
    n = _roll_prior(ok.astype(float)); sx = _roll_prior(x); sy = _roll_prior(y)
    sxx = _roll_prior(x * x); sxy = _roll_prior(x * y)
    varx = sxx / n - (sx / n) ** 2
    cov = sxy / n - (sx / n) * (sy / n)
    good = (n >= MIN_OBS) & np.isfinite(varx) & (varx > 1e-18)
    beta = (cov / varx).where(good)
    alpha = (sy / n - beta * sx / n).where(good)
    e = R - alpha - beta * M
    ef = e.notna()
    ne = _roll_prior(ef.astype(float)); s1 = _roll_prior(e); s2 = _roll_prior(e * e)
    var_e = s2 / ne - (s1 / ne) ** 2
    sig = np.sqrt(var_e.clip(lower=0)).where((ne >= MIN_OBS) & np.isfinite(var_e) & (var_e > 1e-18))
    e3 = e + e.shift(1) + e.shift(2)                       # NaN unless all three are finite
    m3 = M + M.shift(1) + M.shift(2)
    z = e3 / (sig * np.sqrt(3))
    return dict(alpha=alpha, beta=beta, e=e, sigma=sig, z=z, m3=m3)


def signals(F, E):
    """+1 / -1 / 0 per grid hour and coin."""
    z, m3 = F['z'], F['m3']
    valid = z.notna() & m3.notna() & (m3.abs() < MKT_SKIP) & E
    return (valid & (z <= -Z_TH)).astype(int) - (valid & (z >= Z_TH)).astype(int)


def prepare(raw, syms):
    """coins dict for X.simulate: dec = (decision_close, side, i_last, dist)."""
    R, E = panel(raw, syms)
    M = loo_market(R, E)
    F = features(R, M)
    sg = signals(F, E)
    coins = {}
    for s in syms:
        a = raw[s]
        pos = pd.Series(np.arange(len(a['t'])), index=pd.DatetimeIndex(a['t']))
        col = sg[s]; hits = col[col != 0]
        dec = []
        for t, sd in hits.items():
            i = int(pos[t])
            d = t.to_datetime64() + HOUR
            if not (np.datetime64(X.T0) <= d - HOUR <= np.datetime64(X.T1)): continue
            dec.append((d, int(sd), i, STOP_ATR * a['atr'][i]))
        coins[s] = dict(a=a, dec=dec)
    return coins, F, sg


def mgmt(a, j0, mode):
    n = min(len(a['t']), int(np.searchsorted(a['t'], a['t'][j0] + (CAP_H + 8) * HOUR)))
    if mode == 'causal': return [[j] for j in range(j0, n)]
    key = pd.to_datetime(a['t'][j0:n]).floor('4h').values
    out, cur = [], [j0]
    for j in range(j0 + 1, n):
        if key[j - j0] == key[j - j0 - 1]: cur.append(j)
        else: out.append(cur); cur = [j]
    out.append(cur)
    return out


def run_lot(a, i_last, side, dist, cost_bp, mode):
    """Entry at 1h index i_last+1 open. Stop dist, 2R target, 12 h cap; no partial/BE."""
    if i_last + 1 >= len(a['t']) or a['t'][i_last + 1] - a['t'][i_last] != HOUR: return dict(refused='missing_next_hour')
    r = a['rule']
    if r is None: return dict(refused='no_rule')
    if not np.isfinite(dist) or dist <= 0: return dict(refused='bad_atr')
    sd = side; fee = FEES[cost_bp]; j0 = i_last + 1
    ref_in = a['o'][j0]; e = ref_in * (1 + sd * SLIP)
    qty = min(np.floor(RISK / dist / r['step'] + 1e-9), np.floor(CAP_NOTIONAL / e / r['step'] + 1e-9)) * r['step']
    if qty < r['min_qty'] or qty * e < r['min_notional']: return dict(refused='below_min')
    stop = e - sd * dist; tp = e + sd * TP_R * dist
    t_in = a['t'][j0]; deadline = t_in + CAP_H * HOUR
    entry_fee = e * qty * fee
    fund = 0.0; fees = entry_fee; slip_cost = sd * (e - ref_in) * qty
    cash = [(pd.Timestamp(t_in), -entry_fee)]; marks = []; ex = None
    for k, idx in enumerate(mgmt(a, j0, mode)):
        o = a['o'][idx[0]]; hi = a['h'][idx].max(); lo = a['l'][idx].min(); c = a['c'][idx[-1]]
        t_open = a['t'][idx[0]]; t_close = a['t'][idx[-1]] + HOUR
        fund += qty * a['c'][idx].sum() * FUND_H
        adv = lo if sd > 0 else hi; fav = hi if sd > 0 else lo
        if k > 0 and sd * (o - stop) <= 0: ex = (o, 'stop_gap', t_open)
        elif sd * (adv - stop) <= 0: ex = (stop, 'stop', t_close)
        elif sd * (fav - tp) >= 0: ex = (tp, 'target', t_close)
        elif t_close >= deadline: ex = (c, 'time', t_close)
        if ex: break
        xm = c * (1 - sd * SLIP)
        marks.append((pd.Timestamp(t_close), -entry_fee - fund + sd * (xm - e) * qty - xm * qty * fee))
    if ex is None:
        ex = (a['c'][-1], 'TERMINAL_OPEN', a['t'][-1] + HOUR)
    ref, why, t_out = ex
    x = ref * (1 - sd * SLIP)
    gross = sd * (x - e) * qty; f2 = x * qty * fee
    fees += f2; slip_cost += sd * (ref - x) * qty
    pnl = gross - fees - fund
    cash.append((pd.Timestamp(t_out), gross - f2 - fund))
    assert abs(sum(v for _, v in cash) - pnl) < 1e-9
    return dict(t_in=pd.Timestamp(t_in), t_out=pd.Timestamp(t_out), side=sd, e_ref=ref_in, e=e, x_ref=ref, x=x, qty=qty,
                q_final=qty, part=None, risk=qty * dist, gross=gross, fees=fees, slip=slip_cost, fund=fund, pnl=pnl,
                entry_fee=entry_fee, cash=cash, why=why, marks=marks, held_h=float((np.datetime64(t_out) - t_in) / HOUR))


def simulate(coins, syms, order, cost_bp, mode):
    saved = X.run_lot
    X.run_lot = run_lot
    try:
        return X.simulate(coins, syms, order, cost_bp, mode)
    finally:
        X.run_lot = saved


def load_all(dirp):
    rules = json.load(open(os.path.join(ROOT, 'data', 'exchange_rules_testnet.json'), encoding='utf-8'))['symbols']
    raw, excluded = {}, {}
    for s in ALL40:
        p = os.path.join(dirp, f'{s}_1h.csv')
        if not os.path.exists(p): excluded[s] = 'missing_csv'; continue
        raw[s] = D.load(dirp, s, rules)
        if raw[s]['rule'] is None: excluded[s] = 'missing_exchange_rule'
    return raw, excluded


def main(dirp, out):
    os.makedirs(out, exist_ok=True)
    raw, excluded = load_all(dirp)
    if excluded: raise SystemExit(f'BLOCKED: {excluded}')
    coins, F, sg = prepare(raw, ALL40)
    weeks = (X.T1 - X.T0).total_seconds() / 604800
    res = dict(contract='RSR-1H', runner_sha256=hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest(),
               contract_sha256=hashlib.sha256(open(os.path.join(HERE, 'contract.json'), 'rb').read()).hexdigest(),
               harness_sha256='8251f9b34d3eef1b1847ecce4141903df7de363155938407fb369168f27cfad7',
               input_sha256={s: hashlib.sha256(open(os.path.join(dirp, f'{s}_1h.csv'), 'rb').read()).hexdigest() for s in raw},
               calendar_weeks=round(weeks, 2),
               signal_counts={s: dict(long=sum(1 for d in c['dec'] if d[1] > 0), short=sum(1 for d in c['dec'] if d[1] < 0))
                              for s, c in coins.items()}, arms={})
    grid = pd.date_range(X.T0, X.T1, freq='h').values
    for arm, syms in ARMS.items():
        base = sorted(syms)
        orders = [base] + [list(np.random.default_rng(20261112 + k).permutation(base)) for k in range(1, X.N_ORDERS)]
        anyel = np.zeros(len(grid), bool)
        for s in syms:
            anyel |= pd.Series(raw[s]['elig'], index=raw[s]['t']).reindex(grid).fillna(False).values.astype(bool)
        elig_weeks = anyel.sum() / 168
        A = res['arms'][arm] = dict(eligible_weeks=round(float(elig_weeks), 2), paths={})
        for mode in ('causal', 'worst'):
            P = A['paths'][mode] = {}
            for cb in (7, 15, 23):
                rows = []
                for k, od in enumerate(orders):
                    tr, sk = simulate(coins, syms, od, cb, mode)
                    sm = X.summarize(tr, sk, weeks, elig_weeks); sm['order'] = k; rows.append(sm)
                    if k == 0:
                        pd.DataFrame([{kk: v for kk, v in t.items() if kk not in ('marks', 'cash')} for t in tr]).to_csv(
                            os.path.join(out, f'{arm}.{mode}.{cb}bp.order0.trades.csv'), index=False)
                m = pd.DataFrame([{k: v for k, v in r.items() if not isinstance(v, dict)} for r in rows])
                q = lambda col: dict(median=round(float(m[col].median()), 4), p10=round(float(m[col].quantile(.1)), 4),
                                     p90=round(float(m[col].quantile(.9)), 4), min=round(float(m[col].min()), 4),
                                     max=round(float(m[col].max()), 4)) if col in m and m[col].notna().any() else None
                P[f'{cb}bp'] = dict(order0=rows[0], across_orders={c: q(c) for c in (
                    'closes', 'closes_per_cal_week', 'closes_per_elig_week', 'meanR_budget', 'meanR_actual', 'early_meanR',
                    'later_meanR', 'exbest_meanR', 'closedR_maxdd', 'marked_dd_R', 'longest_underwater_days')},
                    share_orders_meanR_pos=round(float((m.meanR_budget > 0).mean()), 3) if 'meanR_budget' in m else 0.0)
                json.dump(res, open(os.path.join(out, 'rsr1h_result.json'), 'w', encoding='utf-8'), indent=1, default=str)
                print(arm, mode, cb, 'done', flush=True)
        A['gates'] = X.gates(A['paths']['causal'], (A['paths']['worst']['15bp']['across_orders'].get('meanR_budget') or {}).get('median'))
        json.dump(res, open(os.path.join(out, 'rsr1h_result.json'), 'w', encoding='utf-8'), indent=1, default=str)
    print('done')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
