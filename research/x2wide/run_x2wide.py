"""X2-WIDE: cross-symbol transfer test of the UNCHANGED CP3 X2 alt/BTC ratio breakout (4h, long+short). RESEARCH ONLY.

Approved by Codex (#219 6827389390) and moe (12 Nov 17:10 Cairo). Contract: research/x2wide/contract.json, frozen with this
runner before any X2-WIDE signal count or outcome. This is a cross-symbol transfer test, not a fresh temporal holdout and
not a new independent strategy. Nothing here touches the engine, runtime or accounts.

Signal and management are read from the frozen module research/x2wide/newstrat_cp3_46ece2b7.py (byte copy of
research/newstrat_cp3.py at 46ece2b7, sha256 929cb91e...). Rule: on UTC 4h bars, long when alt/BTC close ratio crosses
above its prior 60-bar max with close > EMA200 and EMA50 > EMA200; short is the mirror. Stop 2.5 ATR(4h, signal bar),
target 3R, cap 30 bars (120 elapsed hours), half off at +1R and the rest to breakeven (entry fill) at +1R.

Execution (the D01 v3 conventions, extended; see contract.json for the full list):
- 4h bars are built from the canonical 1h CSVs (UTC bins 00/04/..); a 4h bar needs all four 1h bars, else it is dropped
  (no signal on it, not used for indicators). Decision at the 4h close; entry at the next 1h OPEN, which must be exactly
  1 h after the decision bar's last 1h bar (else refused 'missing_next_hour').
- Fixed $8 risk: qty = floor(8 / dist / step) * step, capped by $3,333.33 notional; dist = 2.5 x ATR4h(signal bar);
  stop/targets placed from the entry FILL. Fills 2 bp adverse; fees 5/13/21 bp of each fill's notional.
- Partial: q1 = floor(qty * 0.5 / step) * step at the +1R level; allowed only if q1 and qty - q1 both meet min_qty and
  min_notional at that price, otherwise no partial (blocked) but breakeven still arms at +1R.
- Breakeven is never retroactive: once +1R is reached in a management bar, the stop moves to the entry fill from the NEXT
  management bar.
- Two management paths, both always run:
  causal: management bars = 1h bars. Per bar: adverse OPEN beyond the stop -> exit at the open (gap, exit time = open);
          stop touched -> exit at the stop (stop-first even if a target is also touched in that bar); else +1R level ->
          partial; then 3R target -> rest; else time cap (first bar whose close >= entry + 120 h) -> rest at the close.
  worst:  identical rules on 4h management bars (the 4h bins of the 1h data from the entry), so every stop/target
          ambiguity inside 4 hours resolves stop-first and breakeven arms only from the next 4h bar.
  Intrabar stop/target/partial exits are timed at the management bar CLOSE.
- Funding stress proxy: 1 bp / 8 h x qty open at the start of the management bar x every held 1h CLOSE, including the
  exit bar, for both sides (a cost on both sides; not native funding).
- Max 1 global position; ties at one decision time are taken in the arm order (40 orders: order 0 alphabetical,
  k>=1 numpy.default_rng(20261112+k).permutation); exit at or before a decision close frees the slot.
- Point-in-time liquidity (alt only; BTC is context): D01 rule at the decision's last 1h bar. Decisions in [T0, T1].
- Cairo-day net realized cash halt at -$120: entry fee on the entry day, partial on its day, rest on the exit day.
- Terminal open lot reserves the slot, is marked at its last close with exit costs, and is excluded from closes.
Usage: python run_x2wide.py <clean_csv_dir> <out_dir>
"""
import sys, os, json, hashlib
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'research', 'h5wide_d01'), HERE]
SRC = os.path.join(HERE, 'newstrat_cp3_46ece2b7.py')
assert hashlib.sha256(open(SRC, 'rb').read()).hexdigest() == '929cb91e95d9ca796a531b0dd8f8ca697f91ba00edc0448508411a7f45207c77'
import strategies as S, newstrat_cp3_46ece2b7 as N3, run_d01_v3 as D

X2 = N3.CANDIDATES['cp_x2_alt_btc_ratio_break_4h']
MG, PARAMS = X2['mgmt'], X2['params']
assert X2['fn'] is N3.sig_ratio_break and X2['tf'] == '4h' and X2['sides'] == 'both'
assert (MG['stop_atr'], MG['tp_r'], MG['max_bars'], MG['tp1_r'], MG['tp1_frac'], MG['be_r']) == (2.5, 3.0, 30, 1.0, 0.5, 1.0)
assert PARAMS == dict(n=60)

ORIG8 = [s for s in D.ORIG if s != 'BTCUSDT']
EXTRA = list(D.EXTRA)
ARMS = dict(original8=ORIG8, extra31=EXTRA, union39=ORIG8 + EXTRA)
T0, T1, SPLIT = D.T0, D.T1, D.SPLIT
RISK, CAP_NOTIONAL, HALT, FUND_H, SLIP, FEES, N_ORDERS = D.RISK, D.CAP_NOTIONAL, D.HALT, D.FUND_H, D.SLIP, D.FEES, D.N_ORDERS
HOUR = np.timedelta64(1, 'h'); H4 = np.timedelta64(4, 'h')
MAX_HOURS = MG['max_bars'] * 4


def to4h(a):
    """a: D.load dict (1h). Returns a 4h frame of complete bins with the index of each bin's last 1h bar."""
    t = pd.to_datetime(a['t'])
    df = pd.DataFrame(dict(t=t, o=a['o'], h=a['h'], l=a['l'], c=a['c'], i=np.arange(len(t))))
    g = df.groupby(t.floor('4h'))
    b = g.agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), i=('i', 'last'), n=('i', 'size'))
    b = b[b.n == 4].reset_index().rename(columns=dict(index='t', t='t'))
    b.columns = ['t', 'o', 'h', 'l', 'c', 'i', 'n']
    b['v'] = 1.0
    return b


def signals(d4, btc4):
    """Unchanged frozen X2 signal on a coin's 4h frame, with BTC's 4h indicator frame as context."""
    x = S.indicators(d4[['t', 'o', 'h', 'l', 'c', 'v']].reset_index(drop=True))
    N3.PANEL['btc'] = btc4
    s = N3.sig_ratio_break(x, PARAMS, {})
    return s['le'].values, s['se'].values, x.atr.values


def prepare(a, btc4):
    """Adds 4h decisions to a 1h coin dict: list of (decision_close, side, entry_1h_index, dist) for eligible signals."""
    d4 = to4h(a)
    le, se, atr = signals(d4, btc4)
    out = []
    for k in np.flatnonzero(le | se):
        dec = d4.t.values[k] + H4
        i_last = int(d4.i.values[k])
        if not (np.datetime64(T0) <= dec - HOUR and dec - HOUR <= np.datetime64(T1)): continue
        if not a['elig'][i_last]: continue
        out.append((dec, 1 if le[k] else -1, i_last, STOP_ATR_MULT * atr[k]))
    return out, d4, le, se


STOP_ATR_MULT = MG['stop_atr']


def mgmt_bars(a, j0, mode):
    """Management bars from 1h index j0: lists of 1h indices (causal: one each; worst: 4h UTC bins)."""
    n = len(a['t'])
    # Speed patch (no rule change; parity-checked against de27e871): a lot always exits by the first bar whose close is
    # >= entry + 120 h, so stop the list after the complete 4h bin holding the first 1h bar at or after entry + 128 h.
    k = int(np.searchsorted(a['t'], a['t'][j0] + (MAX_HOURS + 8) * HOUR))
    if k < n:
        n = min(n, int(np.searchsorted(a['t'], pd.Timestamp(a['t'][k]).floor('4h').to_datetime64() + H4)))
    if mode == 'causal': return [[j] for j in range(j0, n)]
    key = pd.to_datetime(a['t'][j0:]).floor('4h').values
    out, cur = [], [j0]
    for j in range(j0 + 1, n):
        if key[j - j0] == key[j - j0 - 1]: cur.append(j)
        else: out.append(cur); cur = [j]
    out.append(cur)
    return out


def run_lot(a, i_last, side, dist, cost_bp, mode):
    """Entry at 1h index i_last+1. side +1 long / -1 short. Returns a lot dict or dict(refused=...)."""
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
    bars = mgmt_bars(a, j0, mode)
    for k, idx in enumerate(bars):
        o = a['o'][idx[0]]; hi = a['h'][idx].max(); lo = a['l'][idx].min(); c = a['c'][idx[-1]]
        t_open = a['t'][idx[0]]; t_close = a['t'][idx[-1]] + HOUR
        bar_fund = q * a['c'][idx].sum() * FUND_H
        fund += bar_fund
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
            elif t_close >= deadline: ex = (c, 'time', t_close)
        if ex:
            ref, why, t_out = ex
            break
        realized = sum(v for _, v in cash) - fund
        xm = c * (1 - sd * SLIP)
        marks.append((pd.Timestamp(t_close), realized + sd * (xm - e) * q - xm * q * fee))
        if tp1_done and not be_armed:                      # +1R reached in this bar: breakeven from the NEXT bar
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
    """coins: {sym: dict(a=1h dict, dec=[(decision_close, side, i_last, dist)])}."""
    syms = [s for s in order if s in syms and s in coins]
    rank = {s: k for k, s in enumerate(syms)}
    events = {}
    for s in syms:
        for dec, sd, i_last, dist in coins[s]['dec']: events.setdefault(dec, []).append((rank[s], s, sd, i_last, dist))
    trades, skips, day_net = [], [], {}
    busy_until = np.datetime64('1970-01-01')
    for dec in sorted(events):
        if dec < busy_until: continue
        if day_net.get(D.cairo_day(dec), 0.0) <= -HALT:
            skips.append(('*', pd.Timestamp(dec), 'daily_halt')); continue
        for _, s, sd, i_last, dist in sorted(events[dec], key=lambda z: z[0]):
            lot = run_lot(coins[s]['a'], i_last, sd, dist, cost_bp, mode)
            if 'refused' in lot:
                skips.append((s, pd.Timestamp(dec), lot['refused'])); continue
            lot.update(sym=s, t_sig=pd.Timestamp(dec), R_budget=lot['pnl'] / RISK, R_actual=lot['pnl'] / lot['risk'])
            trades.append(lot)
            if lot['why'] == 'TERMINAL_OPEN':
                busy_until = np.datetime64('2262-01-01')
                k = D.cairo_day(lot['t_in']); day_net[k] = day_net.get(k, 0.0) + lot['cash'][0][1]
            else:
                busy_until = np.datetime64(lot['t_out'])
                for tm, v in lot['cash']:
                    k = D.cairo_day(tm); day_net[k] = day_net.get(k, 0.0) + v
            break
    return trades, skips


def summarize(trades, skips, weeks, elig_weeks):
    s = D.summarize([{k: v for k, v in t.items() if k not in ('cash', 'part')} for t in trades], skips, weeks, elig_weeks)
    if trades:
        df = pd.DataFrame([dict(side=t['side'], why=t['why'], R=t['R_budget'], part=bool(t['part'] and t['part'].get('qty')))
                           for t in trades])
        cl = df[df.why != 'TERMINAL_OPEN']
        s['by_side'] = {('long' if k > 0 else 'short'): dict(n=int(len(g)), meanR=round(float(g.R.mean()), 4))
                        for k, g in cl.groupby('side')}
        s['partials'] = int(cl.part.sum())
    return s


def gates(cs, worst15):
    """Codex PAPER-review gates (#219 6827389390). cs: {cost: {'across_orders': ..., 'order0': ...}} of the causal path."""
    a15, a23 = cs['15bp']['across_orders'], cs['23bp']['across_orders']
    o15 = cs['15bp']['order0']
    med = lambda a, k: (a.get(k) or {}).get('median')
    pos = lambda v: v is not None and v > 0
    g = dict(closes_ge_100=(med(a15, 'closes') or 0) >= 100,
             each_period_ge_30=min(o15.get('early_n', 0), o15.get('later_n', 0)) >= 30,
             R15_early_pos=pos(med(a15, 'early_meanR')),
             R15_later_pos=pos(med(a15, 'later_meanR')),
             R23_overall_pos=pos(med(a23, 'meanR_budget')),
             exbest15_pos=pos(med(a15, 'exbest_meanR')),
             p10_R15_pos=pos((a15.get('meanR_budget') or {}).get('p10')),
             worst_path_R15_pos=pos(worst15))
    g['PASS'] = all(g.values())
    return g


def load_all(dirp):
    rules = json.load(open(os.path.join(ROOT, 'data', 'exchange_rules_testnet.json'), encoding='utf-8'))['symbols']
    raw, excluded = {}, {}
    for s in D.ORIG + EXTRA:
        p = os.path.join(dirp, f'{s}_1h.csv')
        if not os.path.exists(p): excluded[s] = 'missing_csv'; continue
        raw[s] = D.load(dirp, s, rules)
        if raw[s]['rule'] is None and s != 'BTCUSDT': excluded[s] = 'missing_exchange_rule'
    b4 = to4h(raw['BTCUSDT'])
    bx = S.indicators(b4[['t', 'o', 'h', 'l', 'c', 'v']].reset_index(drop=True))
    btc4 = bx.set_index(pd.to_datetime(bx.t))
    coins = {}
    for s in ORIG8 + EXTRA:
        if s not in raw: continue
        dec, _, _, _ = prepare(raw[s], btc4)
        coins[s] = dict(a=raw[s], dec=dec)
    return raw, coins, excluded


def main(dirp, out):
    os.makedirs(out, exist_ok=True)
    raw, coins, excluded = load_all(dirp)
    weeks = (T1 - T0).total_seconds() / 604800
    res = dict(contract='X2-WIDE', runner_sha256=hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest(),
               contract_sha256=hashlib.sha256(open(os.path.join(HERE, 'contract.json'), 'rb').read()).hexdigest(),
               signal_module_sha256='929cb91e95d9ca796a531b0dd8f8ca697f91ba00edc0448508411a7f45207c77',
               input_sha256={s: hashlib.sha256(open(os.path.join(dirp, f'{s}_1h.csv'), 'rb').read()).hexdigest() for s in raw},
               excluded=excluded, calendar_weeks=round(weeks, 2),
               signal_counts={s: dict(long=sum(1 for d in c['dec'] if d[1] > 0), short=sum(1 for d in c['dec'] if d[1] < 0))
                              for s, c in coins.items()}, arms={})
    grid = pd.date_range(T0, T1, freq='h').values
    for arm, syms in ARMS.items():
        base = sorted(syms)
        orders = [base] + [list(np.random.default_rng(20261112 + k).permutation(base)) for k in range(1, N_ORDERS)]
        anyel = np.zeros(len(grid), bool)
        for s in syms:
            if s in raw:
                anyel |= pd.Series(raw[s]['elig'], index=raw[s]['t']).reindex(grid).fillna(False).values.astype(bool)
        elig_weeks = anyel.sum() / 168
        A = res['arms'][arm] = dict(status='INCOMPLETE' if any(s in excluded for s in syms) else 'COMPLETE',
                                    eligible_weeks=round(float(elig_weeks), 2), paths={})
        for mode in ('causal', 'worst'):
            P = A['paths'][mode] = {}
            for cb in (7, 15, 23):
                rows = []
                for k, od in enumerate(orders):
                    tr, sk = simulate(coins, syms, od, cb, mode)
                    sm = summarize(tr, sk, weeks, elig_weeks); sm['order'] = k; rows.append(sm)
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
                json.dump(res, open(os.path.join(out, 'x2wide_result.json'), 'w', encoding='utf-8'), indent=1, default=str)
        A['gates'] = gates(A['paths']['causal'], (A['paths']['worst']['15bp']['across_orders'].get('meanR_budget') or {}).get('median'))
        json.dump(res, open(os.path.join(out, 'x2wide_result.json'), 'w', encoding='utf-8'), indent=1, default=str)
    print('done')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
