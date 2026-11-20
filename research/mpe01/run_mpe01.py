"""MPE-01: strict passive-limit entry vs matched taker entry for the FROZEN X2 signal on original8. RESEARCH ONLY.

Approved by Codex (#219 6829646500, PR #337 6829651990) and moe (12 Nov 18:40 Cairo). Contract: research/mpe01/contract.json,
frozen with this runner, its tests and the 15m input hashes before any MPE-01 signal count or outcome. This is an
execution-cost hypothesis on an existing signal, NOT a new independent alpha, and it does not rewrite X2-WIDE's failed
verdict. Nothing here touches the engine, runtime, UI, keys or accounts.

Signals: research/x2wide/run_x2wide.py (a525ed69, sha256 8251f9b3...) imported unchanged; X2 4h decisions are built from
the canonical 1h CSVs exactly as in X2-WIDE (same D01 point-in-time eligibility). Execution and management use the cached
15m bars (byte-identical in two PC caches; verified against canonical 1h before freezing, see DATA_QUALITY.md).

Clock (both arms): decision D = X2 4h close. Post time P = D + 15 min (one full 15m bar of latency). 15m bar k covers
[t_k, t_k + 15 min).
MAKER arm (one setting, no search): limit L = signal-bar 4h close - 0.25 x ATR4h (long, floored to tick) or + 0.25 x ATR4h
(short, ceiled to tick). qty is sized from L with the unchanged X2 stop distance dist = 2.5 x ATR4h (fixed $8 risk, floor
step, $3,333.33 notional cap, current min_qty/min_notional). Post-only: if the OPEN of the 15m bar at P is at or through L
(long: open <= L; short: open >= L) the order is rejected ('postonly_cross'). Otherwise the order works for the four 15m
bars starting at P and then expires. A bar fills it only if price trades THROUGH L by >= 2 ticks (long: low <= L - 2 tick;
short: high >= L + 2 tick) AND that bar's base volume >= 100 x qty. A touch never fills. Fill price = L (no slippage).
Fill bar (conservative): if its adverse extreme reaches the stop, exit at the stop at the bar close; no partial, no
target, no breakeven can be booked in the fill bar. Management continues from the next 15m bar.
TAKER control: market entry at the OPEN of the 15m bar at P with 2 bp adverse slippage; same dist, sizing and exits;
the entry bar is managed normally (as in X2: stop-first, then +1R partial, then 3R target).
Exits (both, unchanged X2): stop at entry fill -/+ dist, half off at +1R (floor step, min checks, else blocked), the rest
to breakeven from the NEXT management bar, 3R target, time cap at the first management-bar close >= P + 120 h. Adverse
open beyond the stop -> exit at that open (not in the first managed bar). Intrabar exits timed at the bar close.
Paths: 'm15' = 15m management bars (primary); 'worst' = the same rules on UTC 4h bins of the 15m bars (diagnostic).
Costs (assumptions, not claims about the account fee or the real queue):
  maker entry 2 bp fee + 2 bp cash queue/adverse-selection penalty, no slippage; every exit taker 5 bp fee + 2 bp slip;
  taker entry 5 bp fee + 2 bp slip. Stress adds 8 / 16 bp fee on EVERY executed side. 'alltaker' = maker fill charged a
  5 bp taker fee + the 2 bp penalty (taker control under 'alltaker' = its base).
Funding stress proxy: 1 bp / 8 h -> qty open at the bar start x close x 1bp/32 per held 15m bar incl. the fill/exit bar.
Portfolio: max 1 global position; a working maker order reserves the slot until fill or expiry; at one decision time
candidates are tried in the arm order (40 orders, X2 seeds); a post-only reject or a refusal tries the next candidate;
an unfilled order does not. Cairo-day net cash halt -$120 (Africa/Cairo IANA). Terminal open lots are marked at the last
15m close with exit costs and excluded from closes. Split 2025-10-01 UTC on entry time; all of it consumedDEV.
Usage: python run_mpe01.py <canonical_1h_clean_dir> <cache_15m_dir> <out_dir>
"""
import sys, os, json, hashlib
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, 'research', 'x2wide'), HERE]
XSRC = os.path.join(ROOT, 'research', 'x2wide', 'run_x2wide.py')
X_SHA = '8251f9b34d3eef1b1847ecce4141903df7de363155938407fb369168f27cfad7'
assert hashlib.sha256(open(XSRC, 'rb').read()).hexdigest() == X_SHA
import run_x2wide as X
D, S, MG = X.D, X.S, X.MG

COINS = list(X.ORIG8)
M15 = np.timedelta64(15, 'm'); HOUR = X.HOUR; H4 = X.H4
LIMIT_ATR, THROUGH_TICKS, VOL_MULT, WORK_BARS = 0.25, 2, 100.0, 4
MAX_HOURS = X.MAX_HOURS
DEC_FROM, DEC_TO = np.datetime64('2024-10-11T20:00'), np.datetime64('2026-09-27T20:00')
SPLIT = pd.Timestamp('2025-10-01')
RISK, CAP_NOTIONAL, HALT, SLIP, N_ORDERS = X.RISK, X.CAP_NOTIONAL, X.HALT, X.SLIP, X.N_ORDERS
FUND_15 = X.FUND_H / 4
BP = 1e-4
# (entry_fee, entry_slip, exit_fee, exit_slip) per arm and scenario; penalty is booked as an entry fee.
COSTS = {'maker': {'base': (4 * BP, 0, 5 * BP, SLIP), 'stress8': (12 * BP, 0, 13 * BP, SLIP),
                   'stress16': (20 * BP, 0, 21 * BP, SLIP), 'alltaker': (7 * BP, 0, 5 * BP, SLIP)},
         'taker': {'base': (5 * BP, SLIP, 5 * BP, SLIP), 'stress8': (13 * BP, SLIP, 13 * BP, SLIP),
                   'stress16': (21 * BP, SLIP, 21 * BP, SLIP), 'alltaker': (5 * BP, SLIP, 5 * BP, SLIP)}}
ANOMALY_HOURS = [np.datetime64(x) for x in ('2024-10-28T20:00', '2024-10-28T21:00', '2025-01-14T13:00', '2025-01-14T15:00')]


def load15(dirp, sym):
    d = pd.read_csv(os.path.join(dirp, f'{sym}_15m.csv'))
    t = pd.to_datetime(d.t.astype('int64'), unit='ms').values
    if len(t) < 2 or (np.diff(t) != M15).any(): raise ValueError(f'{sym}: 15m bars not contiguous/unique/sorted')
    return dict(t=t, o=d.o.values.astype(float), h=d.h.values.astype(float), l=d.l.values.astype(float),
                c=d.c.values.astype(float), v=d.v.values.astype(float))


def to_tick(x, tick, up):
    k = x / tick
    return (np.ceil(k - 1e-9) if up else np.floor(k + 1e-9)) * tick


def size(e, dist, r):
    qty = min(np.floor(RISK / dist / r['step'] + 1e-9), np.floor(CAP_NOTIONAL / e / r['step'] + 1e-9)) * r['step']
    return qty if qty >= r['min_qty'] and qty * e >= r['min_notional'] else None


def groups(b, j0, deadline, mode):
    """Management bar groups (lists of 15m indices) from j0; bounded just past the deadline."""
    n = min(len(b['t']), int(np.searchsorted(b['t'], deadline)) + 17)
    if mode == 'm15': return [[j] for j in range(j0, n)]
    key = pd.to_datetime(b['t'][j0:n]).floor('4h').values
    out, cur = [], [j0]
    for j in range(j0 + 1, n):
        if key[j - j0] == key[j - j0 - 1]: cur.append(j)
        else: out.append(cur); cur = [j]
    out.append(cur)
    return out


def manage(b, r, sd, e, ref_in, dist, qty, j0, deadline, costs, mode, fill_bar_conservative):
    """Shared X2 exit engine on 15m data. Entry at price e for bar j0 (filled during j0 if fill_bar_conservative)."""
    fee_in, slip_in, fee_out, slip_out = costs
    stop0 = e - sd * dist; lv1 = e + sd * MG['tp1_r'] * dist; tp = e + sd * MG['tp_r'] * dist
    entry_fee = e * qty * fee_in; t_in = b['t'][j0]
    q = qty; stop = stop0; be_armed = False; tp1_done = False
    fund = 0.0; gross = 0.0; fees = entry_fee; slip_cost = sd * (e - ref_in) * qty
    cash = [(pd.Timestamp(t_in), -entry_fee)]; marks = []; part = None; ex = None
    if fill_bar_conservative:
        bars = [[j0]] + groups(b, j0 + 1, deadline, mode) if j0 + 1 < len(b['t']) else [[j0]]
    else:
        bars = groups(b, j0, deadline, mode)
    for k, idx in enumerate(bars):
        o = b['o'][idx[0]]; hi = b['h'][idx].max(); lo = b['l'][idx].min(); c = b['c'][idx[-1]]
        t_open = b['t'][idx[0]]; t_close = b['t'][idx[-1]] + M15
        fund += q * b['c'][idx].sum() * FUND_15
        adv = lo if sd > 0 else hi; fav = hi if sd > 0 else lo
        entry_bar = fill_bar_conservative and k == 0
        if k > 0 and sd * (o - stop) <= 0: ex = (o, 'be_gap' if be_armed else 'stop_gap', t_open)
        elif sd * (adv - stop) <= 0: ex = (stop, ('be' if be_armed else 'stop') + ('_fillbar' if entry_bar else ''), t_close)
        elif not entry_bar:
            if sd * (fav - lv1) >= 0 and not tp1_done:
                tp1_done = True
                q1 = np.floor(qty * MG['tp1_frac'] / r['step'] + 1e-9) * r['step']; rest = qty - q1
                if (q1 >= r['min_qty'] and rest >= r['min_qty'] - 1e-12 and q1 * lv1 >= r['min_notional']
                        and rest * lv1 >= r['min_notional']):
                    x1 = lv1 * (1 - sd * slip_out)
                    g1 = sd * (x1 - e) * q1; f1 = x1 * q1 * fee_out
                    gross += g1; fees += f1; slip_cost += sd * (lv1 - x1) * q1
                    cash.append((pd.Timestamp(t_close), g1 - f1)); part = dict(t=pd.Timestamp(t_close), qty=q1, x=x1); q = rest
                else:
                    part = dict(t=pd.Timestamp(t_close), qty=0.0, blocked=True)
            if sd * (fav - tp) >= 0: ex = (tp, 'target', t_close)
            elif t_close >= deadline: ex = (c, 'time', t_close)
        if ex: break
        realized = sum(v for _, v in cash) - fund; xm = c * (1 - sd * slip_out)
        marks.append((pd.Timestamp(t_close), realized + sd * (xm - e) * q - xm * q * fee_out))
        if tp1_done and not be_armed: be_armed = True; stop = e
    if ex is None: ex = (b['c'][-1], 'TERMINAL_OPEN', b['t'][-1] + M15)
    ref, why, t_out = ex
    x = ref * (1 - sd * slip_out); g2 = sd * (x - e) * q; f2 = x * q * fee_out
    gross += g2; fees += f2; slip_cost += sd * (ref - x) * q
    pnl = gross - fees - fund
    cash.append((pd.Timestamp(t_out), g2 - f2 - fund))
    assert abs(sum(v for _, v in cash) - pnl) < 1e-9
    return dict(t_in=pd.Timestamp(t_in), t_out=pd.Timestamp(t_out), side=sd, e_ref=ref_in, e=e, x_ref=ref, x=x, qty=qty,
                q_final=q, part=part, risk=qty * dist, gross=gross, fees=fees, slip=slip_cost, fund=fund, pnl=pnl,
                entry_fee=entry_fee, cash=cash, why=why, marks=marks, held_h=float((np.datetime64(t_out) - t_in) / HOUR))


def post_index(b, dec):
    jp = int(np.searchsorted(b['t'], dec + M15))
    if jp + WORK_BARS > len(b['t']) or b['t'][jp] != dec + M15: return None
    return jp


def maker_order(b, r, sd, p_sig, atr, dist):
    """Places the one passive order. Returns dict(status=..., ...); status in refused/postonly_cross/unfilled/filled."""
    if r is None: return dict(status='refused', why='no_rule')
    if not (np.isfinite(dist) and dist > 0): return dict(status='refused', why='bad_atr')
    jp = b['jp']
    if jp is None: return dict(status='refused', why='missing_15m')
    tick = r['tick']
    L = to_tick(p_sig - sd * LIMIT_ATR * atr, tick, up=sd < 0)
    qty = size(L, dist, r)
    if qty is None: return dict(status='refused', why='below_min')
    o = b['o'][jp]
    if sd * (o - L) <= 0: return dict(status='postonly_cross', L=L, qty=qty, t_post=b['t'][jp], t_free=b['t'][jp])
    through = L - sd * THROUGH_TICKS * tick
    touched = False; thru = False
    for j in range(jp, jp + WORK_BARS):
        adv_px = b['l'][j] if sd > 0 else b['h'][j]
        if sd * (adv_px - L) <= 0: touched = True
        if sd * (adv_px - through) <= 0:
            thru = True
            if b['v'][j] >= VOL_MULT * qty:
                return dict(status='filled', L=L, qty=qty, jf=j, t_post=b['t'][jp])
    why = 'volume_short' if thru else ('touch_only' if touched else 'not_reached')
    return dict(status='unfilled', why=why, L=L, qty=qty, t_post=b['t'][jp], t_free=b['t'][jp + WORK_BARS - 1] + M15)


def run_signal(c, s, sd, i_last, dist, arm, scen, mode):
    """One decision for one coin. Returns ('trade', lot) | ('order', record) | ('skip', reason)."""
    a, b, r = c['a'], c['b'], c['a']['rule']
    dec = c['dec_of'][i_last]
    b['jp'] = post_index(b, dec)
    t_post = dec + M15; deadline = t_post + MAX_HOURS * HOUR
    if arm == 'taker':
        if r is None: return ('skip', 'no_rule')
        if not (np.isfinite(dist) and dist > 0): return ('skip', 'bad_atr')
        if b['jp'] is None: return ('skip', 'missing_15m')
        ref_in = b['o'][b['jp']]; e = ref_in * (1 + sd * COSTS['taker'][scen][1])
        qty = size(e, dist, r)
        if qty is None: return ('skip', 'below_min')
        lot = manage(b, r, sd, e, ref_in, dist, qty, b['jp'], deadline, COSTS['taker'][scen], mode, False)
        lot['fill'] = 'taker'; return ('trade', lot)
    od = maker_order(b, r, sd, a['c'][i_last], dist / X.STOP_ATR_MULT, dist)
    if od['status'] == 'refused': return ('skip', od['why'])
    if od['status'] == 'postonly_cross': return ('reject', od)
    if od['status'] == 'unfilled': return ('order', od)
    lot = manage(b, r, sd, od['L'], od['L'], dist, od['qty'], od['jf'], deadline, COSTS['maker'][scen], mode, True)
    lot['fill'] = 'maker'; lot['t_post'] = pd.Timestamp(od['t_post']); return ('trade', lot)


def simulate(coins, order, arm, scen, mode):
    syms = [s for s in order if s in coins]
    rank = {s: k for k, s in enumerate(syms)}
    events = {}
    for s in syms:
        for dec, sd, i_last, dist in coins[s]['dec']: events.setdefault(dec, []).append((rank[s], s, sd, i_last, dist))
    trades, skips, orders, day_net = [], [], [], {}
    busy_until = np.datetime64('1970-01-01')
    for dec in sorted(events):
        if dec < busy_until: continue
        if day_net.get(D.cairo_day(dec), 0.0) <= -HALT:
            skips.append(('*', pd.Timestamp(dec), 'daily_halt')); continue
        for _, s, sd, i_last, dist in sorted(events[dec], key=lambda z: z[0]):
            kind, x = run_signal(coins[s], s, sd, i_last, dist, arm, scen, mode)
            if kind == 'skip': skips.append((s, pd.Timestamp(dec), x)); continue
            if kind == 'reject':
                orders.append(dict(sym=s, t_sig=pd.Timestamp(dec), side=sd, status='postonly_cross')); continue
            if kind == 'order':
                orders.append(dict(sym=s, t_sig=pd.Timestamp(dec), side=sd, status='unfilled', why=x['why']))
                busy_until = x['t_free']; break
            lot = x; lot.update(sym=s, t_sig=pd.Timestamp(dec), R_budget=lot['pnl'] / RISK, R_actual=lot['pnl'] / lot['risk'])
            trades.append(lot)
            if arm == 'maker': orders.append(dict(sym=s, t_sig=pd.Timestamp(dec), side=sd, status='filled'))
            if lot['why'] == 'TERMINAL_OPEN':
                busy_until = np.datetime64('2262-01-01')
                k = D.cairo_day(lot['t_in']); day_net[k] = day_net.get(k, 0.0) + lot['cash'][0][1]
            else:
                busy_until = np.datetime64(lot['t_out'])
                for tm, v in lot['cash']:
                    k = D.cairo_day(tm); day_net[k] = day_net.get(k, 0.0) + v
            break
    return trades, skips, orders


def summarize(trades, skips, orders, weeks):
    old = D.SPLIT; D.SPLIT = SPLIT
    try:
        s = X.summarize(trades, skips, weeks, weeks)
    finally:
        D.SPLIT = old
    st = pd.Series([o['status'] for o in orders]).value_counts().to_dict() if orders else {}
    posted = st.get('filled', 0) + st.get('unfilled', 0)
    s['orders'] = dict(st, posted=posted, fill_rate=round(st.get('filled', 0) / posted, 4) if posted else None,
                       unfilled_why=pd.Series([o['why'] for o in orders if o['status'] == 'unfilled']).value_counts().to_dict())
    s['why_fillbar_stops'] = int(sum(1 for t in trades if t['why'] == 'stop_fillbar'))
    s['anomaly_hour_lots'] = int(sum(1 for t in trades if any(t['t_in'] <= pd.Timestamp(h) + pd.Timedelta(hours=1)
                                                              and pd.Timestamp(h) <= t['t_out'] for h in ANOMALY_HOURS)))
    return s


def signal_level(coins, scen, mode):
    """Portfolio-free matched diagnostic: every eligible signal run alone under both arms."""
    rows = []
    for s, c in coins.items():
        for dec, sd, i_last, dist in c['dec']:
            k1, m = run_signal(c, s, sd, i_last, dist, 'maker', scen, mode)
            k2, t = run_signal(c, s, sd, i_last, dist, 'taker', scen, mode)
            st = {'trade': 'filled', 'order': 'unfilled', 'reject': 'postonly_cross', 'skip': 'refused'}[k1]
            rows.append(dict(sym=s, t_sig=pd.Timestamp(dec), side=sd, maker=st,
                             maker_R=m['pnl'] / RISK if k1 == 'trade' and m['why'] != 'TERMINAL_OPEN' else np.nan,
                             taker_R=t['pnl'] / RISK if k2 == 'trade' and t['why'] != 'TERMINAL_OPEN' else np.nan))
    df = pd.DataFrame(rows)
    out = dict(n=len(df), by_maker_status={})
    for st, g in df.groupby('maker'):
        out['by_maker_status'][st] = dict(n=int(len(g)), taker_meanR=round(float(g.taker_R.mean()), 4) if g.taker_R.notna().any() else None,
                                          maker_meanR=round(float(g.maker_R.mean()), 4) if g.maker_R.notna().any() else None)
    out['taker_all_meanR'] = round(float(df.taker_R.mean()), 4) if len(df) else None
    return out, df


def load_all(dir1h, dir15):
    rules = json.load(open(os.path.join(ROOT, 'data', 'exchange_rules_testnet.json'), encoding='utf-8'))['symbols']
    raw = {s: D.load(dir1h, s, rules) for s in ['BTCUSDT'] + COINS}
    b4 = X.to4h(raw['BTCUSDT'])
    bx = S.indicators(b4[['t', 'o', 'h', 'l', 'c', 'v']].reset_index(drop=True))
    btc4 = bx.set_index(pd.to_datetime(bx.t))
    coins = {}
    for s in COINS:
        dec, _, _, _ = X.prepare(raw[s], btc4)
        dec = [d for d in dec if DEC_FROM <= d[0] <= DEC_TO]
        coins[s] = dict(a=raw[s], b=load15(dir15, s), dec=dec, dec_of={d[2]: d[0] for d in dec})
    return coins


def main(dir1h, dir15, out):
    os.makedirs(out, exist_ok=True)
    want = json.load(open(os.path.join(HERE, 'contract.json'), encoding='utf-8'))['execution_data']['sha256']
    for s in COINS:
        assert hashlib.sha256(open(os.path.join(dir15, f'{s}_15m.csv'), 'rb').read()).hexdigest() == want[s], s
    coins = load_all(dir1h, dir15)
    weeks = float((DEC_TO - DEC_FROM) / np.timedelta64(7, 'D'))
    res = dict(contract='MPE-01', runner_sha256=hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest(),
               contract_sha256=hashlib.sha256(open(os.path.join(HERE, 'contract.json'), 'rb').read()).hexdigest(),
               input_15m_sha256={s: hashlib.sha256(open(os.path.join(dir15, f'{s}_15m.csv'), 'rb').read()).hexdigest() for s in COINS},
               calendar_weeks=round(weeks, 2),
               signal_counts={s: dict(long=sum(1 for d in c['dec'] if d[1] > 0), short=sum(1 for d in c['dec'] if d[1] < 0))
                              for s, c in coins.items()}, paths={}, signal_level={})
    base = sorted(COINS)
    orders = [base] + [list(np.random.default_rng(20261112 + k).permutation(base)) for k in range(1, N_ORDERS)]
    for mode in ('m15', 'worst'):
        sl, df = signal_level(coins, 'base', mode)
        res['signal_level'][mode] = sl; df.to_csv(os.path.join(out, f'signal_level.{mode}.base.csv'), index=False)
        P = res['paths'][mode] = {}
        for arm in ('maker', 'taker'):
            for scen in ('base', 'stress8', 'stress16', 'alltaker'):
                if arm == 'taker' and scen == 'alltaker': continue
                rows = []
                for k, od in enumerate(orders):
                    tr, sk, ords = simulate(coins, od, arm, scen, mode)
                    sm = summarize(tr, sk, ords, weeks); sm['order'] = k; rows.append(sm)
                    if k == 0:
                        pd.DataFrame([{kk: v for kk, v in t.items() if kk not in ('marks', 'cash')} for t in tr]).to_csv(
                            os.path.join(out, f'{mode}.{arm}.{scen}.order0.trades.csv'), index=False)
                        if ords: pd.DataFrame(ords).to_csv(os.path.join(out, f'{mode}.{arm}.{scen}.order0.orders.csv'), index=False)
                m = pd.DataFrame([{k: v for k, v in r.items() if not isinstance(v, dict)} for r in rows])
                q = lambda col: dict(median=round(float(m[col].median()), 4), p10=round(float(m[col].quantile(.1)), 4),
                                     p90=round(float(m[col].quantile(.9)), 4), min=round(float(m[col].min()), 4),
                                     max=round(float(m[col].max()), 4)) if col in m and m[col].notna().any() else None
                P[f'{arm}.{scen}'] = dict(order0=rows[0], across_orders={c: q(c) for c in (
                    'closes', 'closes_per_cal_week', 'meanR_budget', 'meanR_actual', 'early_meanR', 'later_meanR',
                    'exbest_meanR', 'closedR_maxdd', 'marked_dd_R', 'longest_underwater_days')},
                    fill_rate_across_orders=dict(median=float(np.median([r['orders']['fill_rate'] for r in rows
                                                                          if r['orders'].get('fill_rate') is not None] or [np.nan]))),
                    share_orders_meanR_pos=round(float((m.meanR_budget > 0).mean()), 3) if 'meanR_budget' in m else 0.0)
                json.dump(res, open(os.path.join(out, 'mpe01_result.json'), 'w', encoding='utf-8'), indent=1, default=str)
                print(mode, arm, scen, 'done', flush=True)
    print('done')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3])
