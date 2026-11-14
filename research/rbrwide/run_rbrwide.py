"""RBR-WIDE: cross-symbol transfer test of the UNCHANGED rank_breadth_reversal_4h SHORT cell. RESEARCH ONLY.

Approved by Codex/moe 12 Nov 18:02 Cairo. Contract: research/rbrwide/contract.json, frozen with this runner before any
RBR-WIDE count or outcome. A cross-symbol transfer test, not a fresh temporal holdout and not a new strategy.

Rule source: research/rbrwide/strategies_646ecc79.py = strategies.py at 646ecc79 (the exact head of the prior Stage-A
evaluation: 76 short closes, +7.33R, +0.096R mean, CORE-20, 2024-10-14..2025-04-01; contaminated context, not evidence).
The signal function, params and mgmt are read from that frozen module. Short side only.
- At each complete 4h bar: rank7 = rank (descending, pandas average ties) of the 42-bar (7-day) return over the ARM's
  coins that have a finite return at that bar time; rank7_n = their count; breadth7 = fraction with return > 0. The
  return uses the coin's own close at t and at t - 168 h on the 4h grid (no compression; identical to the frozen
  index-based ret42 on contiguous data, which the canonical set is inside each coin's listed range).
- Entry (frozen se): rank7_n >= 8, top ceil(20% x n) rank, breadth7 >= 0.65, RSI2 crosses below 70 on a red candle,
  volume >= 80% of the prior-42 median, 1.5 ATR stop >= 5 x 2 x 7 bp. Signal exit (frozen sx): rank7 >= ceil(n/2) or
  breadth7 < 0.45 (an unranked coin has rank 999 -> exit), checked at every 4h close after the intrabar events, as
  backtest.run step 6.
- Mgmt (frozen): stop 1.5 ATR(4h, signal bar), half at +1R, breakeven at +1R, target 2R, max_bars 18 = backtest.run's
  'i - i_entry >= 18' -> exit at the close of the 4h bar entry+18 = entry + 76 elapsed hours.
Execution: the X2-WIDE harness conventions (research/x2wide/run_x2wide.py at a525ed69, imported unchanged): next-1h-open
entry, fixed $8, fills 2 bp adverse, fees 5/13/21 bp, partial floor-to-step with min checks, breakeven never retroactive,
causal 1h path and worst 4h stop-first path, funding stress proxy 1 bp/8h on held 1h closes (a cost for shorts too),
max 1 global position, 40 tie orders, D01 PIT liquidity per coin, Cairo-day cash halt, terminal-open censoring.
Usage: python run_rbrwide.py <clean_csv_dir> <out_dir>
"""
import sys, os, json, hashlib
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, 'research', 'x2wide'), HERE]
SRC = os.path.join(HERE, 'strategies_646ecc79.py')
assert hashlib.sha256(open(SRC, 'rb').read()).hexdigest() == 'ddaee353f6047c915f966cd0ebeb748e75569c039f752f479d6acdd1b96881bf'
XSRC = os.path.join(ROOT, 'research', 'x2wide', 'run_x2wide.py')
assert hashlib.sha256(open(XSRC, 'rb').read()).hexdigest() == '8251f9b34d3eef1b1847ecce4141903df7de363155938407fb369168f27cfad7'
import strategies_646ecc79 as SF
import run_x2wide as X

D = X.D
ST = SF.STRATEGIES['rank_breadth_reversal_4h']
MG, PARAMS = ST['mgmt'], ST['params']
assert ST['fn'] is SF.sig_rank_breadth_reversal_4h and ST['tf'] == '4h'
assert MG == dict(stop_atr=1.5, tp1_r=1.0, tp1_frac=0.5, be_r=1.0, tp_r=2.0, max_bars=18)
assert PARAMS == dict(rank_frac=0.20, breadth_low=0.35, breadth_high=0.65, rsi_low=30.0, rsi_high=70.0,
                      volume_floor=0.80, cost_multiple=5.0)

CORE20 = ['BTCUSDT', 'ETHUSDT', 'ZECUSDT', 'SOLUSDT', 'XRPUSDT', 'NEARUSDT', 'HYPEUSDT', 'DOGEUSDT', 'UNIUSDT', 'SUIUSDT',
          'QNTUSDT', 'BNBUSDT', 'LSKUSDT', 'ENAUSDT', '1000PEPEUSDT', 'WLDUSDT', 'PUMPUSDT', 'ADAUSDT', 'AVAXUSDT',
          'LINKUSDT']                                              # frozen static universe core_20 (run_vwaprev frozen_universe)
CANON40 = D.ORIG + D.EXTRA
CONTROL = [s for s in CORE20 if s in CANON40]                      # 17: HYPE, LSK, PUMP are not in the canonical set
MISSING_CORE20 = [s for s in CORE20 if s not in CANON40]
COMPLEMENT = [s for s in CANON40 if s not in CORE20]               # 23 coins never in the RBR evaluation universe
ARMS = dict(core17_control=CONTROL, complement23=COMPLEMENT, union40=CANON40)
SIDE = -1
MAX_HOURS = (MG['max_bars'] + 1) * 4                               # 76 h, backtest.run parity
STAGEA_WINDOW = (pd.Timestamp('2024-10-14'), pd.Timestamp('2025-04-01'))
HOUR, H4 = X.HOUR, X.H4
SLIP, FEES, FUND_H, RISK, CAP_NOTIONAL = X.SLIP, X.FEES, X.FUND_H, X.RISK, X.CAP_NOTIONAL
T0, T1 = X.T0, X.T1


def frames4h(raw, syms):
    """Complete 4h frames with frozen indicators for the arm's coins."""
    out = {}
    for s in syms:
        d4 = X.to4h(raw[s]).copy()
        d4['v'] = raw[s]['v4'].reindex(pd.DatetimeIndex(d4.t)).values
        x = SF.indicators(d4[['t', 'o', 'h', 'l', 'c', 'v']].reset_index(drop=True))
        x['i'] = d4['i'].values
        out[s] = x
    return out


def cross_section(fr):
    """rank7 / rank7_n / breadth7 on the 4h grid over the arm's coins (frozen build_context lines, time-based return)."""
    r42 = {}
    for s, x in fr.items():
        c = pd.Series(x.c.values, index=pd.DatetimeIndex(x.t))
        prev = c.reindex(c.index - pd.Timedelta(hours=168)).values
        r42[s] = pd.Series(c.values / prev - 1, index=c.index)
    r42 = pd.DataFrame(r42).sort_index()
    r7 = r42.rank(axis=1, ascending=False)
    r7n = r42.notna().sum(axis=1)
    breadth7 = (r42 > 0).sum(axis=1) / r7n.replace(0, np.nan)
    return r7, r7n, breadth7


def signals(fr):
    r7, r7n, b7 = cross_section(fr)
    out = {}
    for s, x in fr.items():
        t = pd.DatetimeIndex(x.t)
        ctx = dict(rank7=pd.Series(r7[s].reindex(t).values, index=x.index).fillna(999),
                   rank7_n=pd.Series(r7n.reindex(t).values, index=x.index).fillna(0),
                   breadth7=pd.Series(b7.reindex(t).values, index=x.index))
        sg = SF.sig_rank_breadth_reversal_4h(x, PARAMS, ctx)
        out[s] = (sg['se'].fillna(False).values.astype(bool), sg['sx'].fillna(False).values.astype(bool))
    return out


def prepare(raw, syms):
    """Per coin: decisions [(decision_close, -1, i_last, dist)] and sx_at_1h (bool per 1h index, at 4h closes only)."""
    fr = frames4h(raw, syms)
    sg = signals(fr)
    coins = {}
    for s in syms:
        x, (se, sx) = fr[s], sg[s]
        a = raw[s]
        sx1 = np.zeros(len(a['t']), bool); sx1[x['i'].values[sx]] = True
        dec = []
        for k in np.flatnonzero(se):
            i_last = int(x['i'].values[k]); dc = np.datetime64(x.t.values[k]) + H4
            if not (np.datetime64(T0) <= dc - HOUR <= np.datetime64(T1)): continue
            if not a['elig'][i_last]: continue
            dec.append((dc, SIDE, i_last, MG['stop_atr'] * x.atr.values[k]))
        coins[s] = dict(a=dict(a, sx1=sx1), dec=dec)
    return coins


def run_lot(a, i_last, side, dist, cost_bp, mode):
    """X2-WIDE run_lot with RBR mgmt and the frozen signal exit at 4h closes (after intrabar events, before time)."""
    if i_last + 1 >= len(a['t']) or a['t'][i_last + 1] - a['t'][i_last] != HOUR: return dict(refused='missing_next_hour')
    r = a['rule']
    if r is None: return dict(refused='no_rule')
    if not np.isfinite(dist) or dist <= 0: return dict(refused='bad_atr')
    sd = side; fee = FEES[cost_bp]; j0 = i_last + 1
    ref_in = a['o'][j0]; e = ref_in * (1 + sd * SLIP)
    qty = min(np.floor(RISK / dist / r['step'] + 1e-9), np.floor(CAP_NOTIONAL / e / r['step'] + 1e-9)) * r['step']
    if qty < r['min_qty'] or qty * e < r['min_notional']: return dict(refused='below_min')
    stop0 = e - sd * dist; lv1 = e + sd * MG['tp1_r'] * dist; tp = e + sd * MG['tp_r'] * dist
    t_in = a['t'][j0]; deadline = t_in + MAX_HOURS * HOUR
    entry_fee = e * qty * fee
    q = qty; stop = stop0; be_armed = False; tp1_done = False
    fund = 0.0; gross = 0.0; fees = entry_fee; slip_cost = (sd * (e - ref_in)) * qty
    cash = [(pd.Timestamp(t_in), -entry_fee)]; marks = []; part = None; why = None
    sx1 = a['sx1']
    for k, idx in enumerate(X.mgmt_bars(a, j0, mode)):
        o = a['o'][idx[0]]; hi = a['h'][idx].max(); lo = a['l'][idx].min(); c = a['c'][idx[-1]]
        t_open = a['t'][idx[0]]; t_close = a['t'][idx[-1]] + HOUR
        fund += q * a['c'][idx].sum() * FUND_H
        adv = lo if sd > 0 else hi; fav = hi if sd > 0 else lo
        ex = None
        if k > 0 and sd * (o - stop) <= 0: ex = (o, 'be_gap' if be_armed else 'stop_gap', t_open)
        elif sd * (adv - stop) <= 0: ex = (stop, 'be' if be_armed else 'stop', t_close)
        else:
            reached1 = sd * (fav - lv1) >= 0
            if reached1 and not tp1_done:
                tp1_done = True
                q1 = np.floor(qty * MG['tp1_frac'] / r['step'] + 1e-9) * r['step']
                rest = qty - q1
                if (q1 >= r['min_qty'] and rest >= r['min_qty'] - 1e-12 and q1 * lv1 >= r['min_notional']
                        and rest * lv1 >= r['min_notional']):
                    x1 = lv1 * (1 - sd * SLIP)
                    g1 = sd * (x1 - e) * q1; f1 = x1 * q1 * fee
                    gross += g1; fees += f1; slip_cost += sd * (lv1 - x1) * q1
                    cash.append((pd.Timestamp(t_close), g1 - f1))
                    part = dict(t=pd.Timestamp(t_close), qty=q1, x=x1)
                    q = rest
                else:
                    part = dict(t=pd.Timestamp(t_close), qty=0.0, blocked=True)
            if sd * (fav - tp) >= 0: ex = (tp, 'target', t_close)
            elif sx1[idx[-1]]: ex = (c, 'signal', t_close)
            elif t_close >= deadline: ex = (c, 'time', t_close)
        if ex:
            ref, why, t_out = ex
            break
        realized = sum(v for _, v in cash) - fund
        xm = c * (1 - sd * SLIP)
        marks.append((pd.Timestamp(t_close), realized + sd * (xm - e) * q - xm * q * fee))
        if tp1_done and not be_armed:
            be_armed = True; stop = e
    else:
        ref, why, t_out = a['c'][-1], 'TERMINAL_OPEN', a['t'][-1] + HOUR
    x = ref * (1 - sd * SLIP)
    g2 = sd * (x - e) * q; f2 = x * q * fee
    gross += g2; fees += f2; slip_cost += sd * (ref - x) * q
    pnl = gross - fees - fund
    cash.append((pd.Timestamp(t_out), g2 - f2 - fund))
    assert abs(sum(v for _, v in cash) - pnl) < 1e-9
    return dict(t_in=pd.Timestamp(t_in), t_out=pd.Timestamp(t_out), side=sd, e_ref=ref_in, e=e, x_ref=ref, x=x, qty=qty,
                q_final=q, part=part, risk=qty * dist, gross=gross, fees=fees, slip=slip_cost, fund=fund, pnl=pnl,
                entry_fee=entry_fee, cash=cash, why=why, marks=marks,
                held_h=float((np.datetime64(t_out) - t_in) / HOUR))


def simulate(coins, syms, order, cost_bp, mode):
    saved = X.run_lot
    X.run_lot = run_lot
    try:
        return X.simulate(coins, syms, order, cost_bp, mode)
    finally:
        X.run_lot = saved


def stagea_window_stats(trades):
    cl = [t for t in trades if t['why'] != 'TERMINAL_OPEN' and STAGEA_WINDOW[0] <= t['t_in'] < STAGEA_WINDOW[1]]
    return dict(n=len(cl), meanR=round(float(np.mean([t['R_budget'] for t in cl])), 4) if cl else None,
                sumR=round(float(np.sum([t['R_budget'] for t in cl])), 2) if cl else 0.0)


def load_all(dirp):
    rules = json.load(open(os.path.join(ROOT, 'data', 'exchange_rules_testnet.json'), encoding='utf-8'))['symbols']
    raw, excluded = {}, {s: 'not_in_canonical_set' for s in MISSING_CORE20}
    for s in CANON40:
        p = os.path.join(dirp, f'{s}_1h.csv')
        if not os.path.exists(p): excluded[s] = 'missing_csv'; continue
        raw[s] = D.load(dirp, s, rules)
        v = pd.read_csv(p, usecols=['t', 'v'])
        v['t'] = pd.to_datetime(v.t, unit='ms'); v = v[v.t <= T1]
        raw[s]['v4'] = v.groupby(v.t.dt.floor('4h')).v.sum()
        if raw[s]['rule'] is None: excluded[s] = 'missing_exchange_rule'
    return raw, excluded


def main(dirp, out):
    os.makedirs(out, exist_ok=True)
    raw, excluded = load_all(dirp)
    weeks = (T1 - T0).total_seconds() / 604800
    res = dict(contract='RBR-WIDE', runner_sha256=hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest(),
               contract_sha256=hashlib.sha256(open(os.path.join(HERE, 'contract.json'), 'rb').read()).hexdigest(),
               signal_module_sha256='ddaee353f6047c915f966cd0ebeb748e75569c039f752f479d6acdd1b96881bf',
               harness_sha256='8251f9b34d3eef1b1847ecce4141903df7de363155938407fb369168f27cfad7',
               input_sha256={s: hashlib.sha256(open(os.path.join(dirp, f'{s}_1h.csv'), 'rb').read()).hexdigest() for s in raw},
               excluded=excluded, calendar_weeks=round(weeks, 2), arms={})
    grid = pd.date_range(T0, T1, freq='h').values
    for arm, syms in ARMS.items():
        syms = [s for s in syms if s in raw]
        coins = prepare(raw, syms)
        base = sorted(syms)
        orders = [base] + [list(np.random.default_rng(20261112 + k).permutation(base)) for k in range(1, X.N_ORDERS)]
        anyel = np.zeros(len(grid), bool)
        for s in syms:
            anyel |= pd.Series(raw[s]['elig'], index=raw[s]['t']).reindex(grid).fillna(False).values.astype(bool)
        elig_weeks = anyel.sum() / 168
        frozen = CORE20 if arm == 'core17_control' else ARMS[arm]
        A = res['arms'][arm] = dict(status='INCOMPLETE' if any(s in excluded for s in frozen) else 'COMPLETE',
                                    coins=syms, eligible_weeks=round(float(elig_weeks), 2),
                                    signal_counts={s: len(c['dec']) for s, c in coins.items()}, paths={})
        for mode in ('causal', 'worst'):
            P = A['paths'][mode] = {}
            for cb in (7, 15, 23):
                rows = []
                for k, od in enumerate(orders):
                    tr, sk = simulate(coins, syms, od, cb, mode)
                    sm = X.summarize(tr, sk, weeks, elig_weeks); sm['order'] = k
                    sm['stagea_window'] = stagea_window_stats(tr)
                    rows.append(sm)
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
                json.dump(res, open(os.path.join(out, 'rbrwide_result.json'), 'w', encoding='utf-8'), indent=1, default=str)
        A['gates'] = X.gates(A['paths']['causal'],
                             (A['paths']['worst']['15bp']['across_orders'].get('meanR_budget') or {}).get('median'))
        json.dump(res, open(os.path.join(out, 'rbrwide_result.json'), 'w', encoding='utf-8'), indent=1, default=str)
    print('done')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
