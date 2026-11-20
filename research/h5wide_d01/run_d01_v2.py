"""H5-WIDE-D01 runner v2 (bug-fix of run_d01.py after Codex pre-economic review, #219 comment 6826926628).

Contract: docs/research/codex_cp4_review_20261112/h5_wide_preregistration.json (evidence 5b768545, sha256 622635c7...).
v1 (run_d01.py at c1de983a) outputs are WITHDRAWN. v2 changes only execution/accounting mechanics; the H5 rule, exits,
universe, arms, split, eligibility thresholds and tie orders are unchanged. No parameter was tuned.

Fixes, by Codex finding:
1. Cairo days come from tz_localize('UTC').tz_convert('Africa/Cairo') of the actual event time (DST-correct).
2. Daily halt uses NET realized P&L of all trades closed in the Cairo day (profits included). All fees and funding of a
   trade are realized at its exit time. No new entry while that day's net realized P&L <= -$120.
3. A lot still open when data ends keeps the slot to the end (no later entries), is marked to market at its coin's last
   close (exit costs applied as if closed there) and is reported as TERMINAL_OPEN, inside the marked curve.
4. Duplicate timestamps raise an error (never silently dropped). Entry needs the coin's next candle exactly 1 h after
   the decision candle, else the entry is refused ('missing_next_hour'). The time cap is 48 elapsed HOURS: the position
   exits at the close of the first candle whose close time is >= entry time + 48 h.
5. Costs: fills are reference price x (1 +/- 2 bp) adverse (entry above, exits below), fees are 5/13/21 bp of each fill's
   notional. Stop and target are placed from the entry FILL: stop = fill - 3 ATR, target = fill + 4 x 3 ATR. Stop exit
   reference = stop level (or the open on an adverse gap), target reference = target level, time reference = close.
6. Marked equity: realized P&L plus the open lot marked at every held candle close; marked max DD, longest underwater
   time (days), terminal MTM. Frequency over calendar weeks and over 'eligible weeks' (hours where at least one arm coin is
   eligible). R is reported two ways: R_budget = P&L / $8, R_actual = P&L / quantized stop risk.
7. Missing CSVs or exchange rules are listed as explicit exclusions and make the arm INCOMPLETE.
Usage: python run_d01_v2.py <clean_csv_dir> <out_dir>
"""
import sys, os, json, hashlib
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'research', 'h5wide')]
SRC = os.path.join(ROOT, 'research', 'h5wide', 'h5_hourly_long_master.py')
assert hashlib.sha256(open(SRC, 'rb').read()).hexdigest() == '8573dd81b39558f55d29682223568d7f72d61e9b25eaa5f072f81dc94139f128'
import strategies as S, h5_hourly_long_master as H5

ORIG = ['ADAUSDT', 'AVAXUSDT', 'BNBUSDT', 'BTCUSDT', 'DOGEUSDT', 'ETHUSDT', 'LINKUSDT', 'SOLUSDT', 'XRPUSDT']
EXTRA = ['1000PEPEUSDT', 'AAVEUSDT', 'APTUSDT', 'ARBUSDT', 'ARKUSDT', 'BCHUSDT', 'DOTUSDT', 'ENAUSDT', 'ENJUSDT',
         'ETCUSDT', 'FETUSDT', 'FILUSDT', 'GALAUSDT', 'INJUSDT', 'LTCUSDT', 'MANAUSDT', 'MOVRUSDT', 'NEARUSDT',
         'ONDOUSDT', 'ONEUSDT', 'QNTUSDT', 'SANDUSDT', 'STRKUSDT', 'SUIUSDT', 'SUPERUSDT', 'TAOUSDT', 'UNIUSDT',
         'WLDUSDT', 'XLMUSDT', 'ZECUSDT', 'ZROUSDT']
PILOT_CONSUMED = {'1000PEPEUSDT', 'ENAUSDT', 'NEARUSDT', 'QNTUSDT', 'SUIUSDT', 'UNIUSDT', 'WLDUSDT', 'ZECUSDT'}
ARMS = dict(original9=ORIG, extra31=EXTRA, union40=ORIG + EXTRA)
T0, T1 = pd.Timestamp('2022-08-26 06:00'), pd.Timestamp('2026-10-04 04:00')
SPLIT = pd.Timestamp('2025-01-01')
RISK, CAP_NOTIONAL, HALT = 8.0, 333.3333 * 10, 120.0
STOP_ATR, TP_R, MAX_HOURS, FUND_H, SLIP = 3.0, 4.0, 48, 0.0001 / 8, 0.0002
FEES = {7: 0.0005, 15: 0.0013, 23: 0.0021}                   # total per side = 2 bp slippage + fee
N_ORDERS = 40
HOUR = np.timedelta64(1, 'h')


def cairo_day(t):
    return pd.Timestamp(t).tz_localize('UTC').tz_convert('Africa/Cairo').date()


def load(dirp, sym, rules):
    d = pd.read_csv(os.path.join(dirp, f'{sym}_1h.csv'))
    d['t'] = pd.to_datetime(d.t, unit='ms')
    if d.t.duplicated().any(): raise ValueError(f'{sym}: duplicate timestamps')
    if not d.t.is_monotonic_increasing: raise ValueError(f'{sym}: timestamps not sorted')
    d = d[d.t <= T1].reset_index(drop=True)                  # history before T0 is warm-up only
    x = S.indicators(d[['t', 'o', 'h', 'l', 'c', 'v']])
    sig = H5.signal(x, {}, {})['le'].values
    day = d.t.dt.floor('D')
    dq = d.groupby(day).qv.sum(); dn = d.groupby(day).size()
    dq = dq.where(dn == 24)
    dq = dq.reindex(pd.date_range(dq.index.min(), dq.index.max(), freq='D'))
    med = dq.rolling(30, min_periods=30).median().shift(1)    # 30 complete UTC days strictly before the decision day
    liq = med.reindex(day).values >= 1e7
    run = d.groupby(d.t.diff().ne(pd.Timedelta(hours=1)).cumsum()).cumcount().values + 1
    elig = liq & (run >= 720)
    t = d.t.values
    return dict(t=t, o=d.o.values, h=d.h.values, l=d.l.values, c=d.c.values, atr=x.atr.values,
                sig=sig & elig & (t >= np.datetime64(T0)), sig_raw=sig, elig=elig, rule=rules.get(sym))


def run_lot(a, i, cost_bp):
    """Open at candle i+1 (decision candle i). Returns dict or None (refused). Exit may be TERMINAL_OPEN."""
    if i + 1 >= len(a['t']) or a['t'][i + 1] - a['t'][i] != HOUR: return dict(refused='missing_next_hour')
    r = a['rule']; dist = STOP_ATR * a['atr'][i]
    if r is None: return dict(refused='no_rule')
    if not np.isfinite(dist) or dist <= 0: return dict(refused='bad_atr')
    ref_in = a['o'][i + 1]; e = ref_in * (1 + SLIP); fee = FEES[cost_bp]
    qty = min(np.floor(RISK / dist / r['step'] + 1e-9), np.floor(CAP_NOTIONAL / e / r['step'] + 1e-9)) * r['step']
    if qty < r['min_qty'] or qty * e < r['min_notional']: return dict(refused='below_min')
    stop, tp = e - dist, e + TP_R * dist
    t_in = a['t'][i + 1]; deadline = t_in + MAX_HOURS * HOUR
    fund = 0.0; marks = []
    for j in range(i + 1, len(a['t'])):
        o, h, l, c, tj = a['o'][j], a['h'][j], a['l'][j], a['c'][j], a['t'][j]
        fund += qty * o * FUND_H * ((tj - a['t'][j - 1]) / HOUR if j > i + 1 else 1.0)
        ex = None
        if j > i + 1 and o <= stop: ex = (o, 'stop_gap')
        elif l <= stop: ex = (stop, 'stop')
        elif h >= tp: ex = (tp, 'target')
        elif tj + HOUR >= deadline: ex = (c, 'time')
        if ex:
            ref, why = ex; break
        marks.append((tj + HOUR, (c * (1 - SLIP) - e) * qty - e * qty * fee - c * qty * fee - fund))
    else:
        ref, why, j = a['c'][-1], 'TERMINAL_OPEN', len(a['t']) - 1
    x = ref * (1 - SLIP)
    gross = (x - e) * qty
    fees = (e * qty + x * qty) * fee
    slip_cost = (e - ref_in) * qty + (ref - x) * qty
    pnl = gross - fees - fund
    return dict(t_in=pd.Timestamp(t_in), t_out=pd.Timestamp(a['t'][j] + HOUR), e_ref=ref_in, e=e, x_ref=ref, x=x, qty=qty,
                risk=qty * dist, gross=gross, fees=fees, slip=slip_cost, fund=fund, pnl=pnl, why=why, marks=marks,
                held_h=float((a['t'][j] + HOUR - t_in) / HOUR))


def simulate(data, syms, order, cost_bp):
    syms = [s for s in order if s in syms and s in data]
    rank = {s: k for k, s in enumerate(syms)}
    events = {}
    for s in syms:
        for i in np.flatnonzero(data[s]['sig']): events.setdefault(data[s]['t'][i], []).append((rank[s], s, i))
    trades, skips, day_net = [], [], {}
    busy_until = np.datetime64('1970-01-01')
    for g in sorted(events):
        dec_close = g + HOUR                                  # decision is taken at the decision candle's close
        if dec_close <= busy_until: continue
        if day_net.get(cairo_day(dec_close), 0.0) <= -HALT:
            skips.append(('*', pd.Timestamp(g), 'daily_halt')); continue
        for _, s, i in sorted(events[g]):
            lot = run_lot(data[s], i, cost_bp)
            if 'refused' in lot:
                skips.append((s, pd.Timestamp(g), lot['refused'])); continue
            lot.update(sym=s, t_sig=pd.Timestamp(g), R_budget=lot['pnl'] / RISK, R_actual=lot['pnl'] / lot['risk'])
            trades.append(lot)
            if lot['why'] == 'TERMINAL_OPEN':
                busy_until = np.datetime64('2262-01-01')       # slot reserved to the end of data
            else:
                busy_until = np.datetime64(lot['t_out'])
                k = cairo_day(lot['t_out']); day_net[k] = day_net.get(k, 0.0) + lot['pnl']
            break
    return trades, skips


def marked_curve(trades):
    pts = []; cum = 0.0
    for tr in trades:
        for tm, u in tr['marks']: pts.append((tm, cum + u))
        if tr['why'] != 'TERMINAL_OPEN': cum += tr['pnl']
        pts.append((tr['t_out'], cum if tr['why'] != 'TERMINAL_OPEN' else cum + tr['pnl']))
    if not pts: return 0.0, 0.0, 0.0
    t = pd.to_datetime([p[0] for p in pts]); v = np.array([p[1] for p in pts])
    peak = np.maximum.accumulate(np.r_[0.0, v])[1:]
    dd = float((peak - v).max())
    uw, start, longest = peak - v > 1e-9, None, pd.Timedelta(0)
    for k in range(len(v)):
        if uw[k] and start is None: start = t[k - 1] if k else t[k]
        if not uw[k] and start is not None: longest = max(longest, t[k] - start); start = None
    if start is not None: longest = max(longest, t[-1] - start)
    return dd, longest.total_seconds() / 86400, float(v[-1])


def summarize(trades, skips, weeks, elig_weeks):
    out = dict(n=len(trades), skips=pd.Series([s[2] for s in skips]).value_counts().to_dict() if skips else {})
    if not trades: return out
    df = pd.DataFrame([{k: v for k, v in t.items() if k != 'marks'} for t in trades])
    closed = df[df.why != 'TERMINAL_OPEN']; Rb = closed.R_budget
    out.update(closes=len(closed), terminal_open=int((df.why == 'TERMINAL_OPEN').sum()),
               terminal_mtm=round(float(df.pnl[df.why == 'TERMINAL_OPEN'].sum()), 2),
               closes_per_cal_week=round(len(closed) / weeks, 3),
               closes_per_elig_week=round(len(closed) / elig_weeks, 3) if elig_weeks else None,
               meanR_budget=round(float(Rb.mean()), 4), meanR_actual=round(float(closed.R_actual.mean()), 4),
               sumR_budget=round(float(Rb.sum()), 2))
    e, l = closed.t_in < SPLIT, closed.t_in >= SPLIT
    out.update(early_n=int(e.sum()), later_n=int(l.sum()),
               early_meanR=round(float(Rb[e].mean()), 4) if e.any() else None,
               later_meanR=round(float(Rb[l].mean()), 4) if l.any() else None)
    by = Rb.groupby(closed.sym).sum().sort_values()
    out['exbest_meanR'] = round(float(Rb[closed.sym != by.index[-1]].mean()), 4) if len(by) > 1 else None
    out['by_coin'] = {s: dict(n=int((closed.sym == s).sum()), sumR=round(float(by[s]), 2),
                              early_sumR=round(float(Rb[(closed.sym == s) & e].sum()), 2),
                              later_sumR=round(float(Rb[(closed.sym == s) & l].sum()), 2)) for s in by.index}
    cum = Rb.cumsum(); out['closedR_maxdd'] = round(float((np.maximum.accumulate(np.r_[0, cum])[1:] - cum).max()), 2)
    dd, uw_days, last = marked_curve(trades)
    out.update(marked_dd_usd=round(dd, 2), marked_dd_R=round(dd / RISK, 2), longest_underwater_days=round(uw_days, 1),
               final_marked_usd=round(last, 2))
    n = max(len(closed), 1)
    out['decomp_R_per_trade'] = dict(gross_after_slip=round(float(closed.gross.sum() / RISK / n), 4),
                                     slippage=round(float(closed.slip.sum() / RISK / n), 4),
                                     fees=round(float(closed.fees.sum() / RISK / n), 4),
                                     funding=round(float(closed.fund.sum() / RISK / n), 4))
    out['risk_usd'] = dict(max=round(float(df.risk.max()), 4), median=round(float(df.risk.median()), 4))
    out['why'] = df.why.value_counts().to_dict()
    return out


def main(dirp, out):
    os.makedirs(out, exist_ok=True)
    rules = json.load(open(os.path.join(ROOT, 'data', 'exchange_rules_testnet.json'), encoding='utf-8'))['symbols']
    data, excluded = {}, {}
    for s in ORIG + EXTRA:
        p = os.path.join(dirp, f'{s}_1h.csv')
        if not os.path.exists(p): excluded[s] = 'missing_csv'; continue
        data[s] = load(dirp, s, rules)
        if data[s]['rule'] is None: excluded[s] = 'missing_exchange_rule'
    weeks = (T1 - T0).total_seconds() / 604800
    res = dict(contract='H5-WIDE-D01', runner='v2', prereg_sha256='622635c784e3fc9e37401623dad9f0c5dadeb3b2386d5a305b7cbd6416ba5174',
               runner_sha256=hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest(),
               input_sha256={s: hashlib.sha256(open(os.path.join(dirp, f'{s}_1h.csv'), 'rb').read()).hexdigest() for s in data},
               excluded=excluded, pilot_consumed=sorted(PILOT_CONSUMED), calendar_weeks=round(weeks, 2), arms={})
    for arm, syms in ARMS.items():
        base = sorted(syms)
        orders = [base] + [list(np.random.default_rng(20261112 + k).permutation(base)) for k in range(1, N_ORDERS)]
        live = [s for s in syms if s in data]
        grid = pd.date_range(T0, T1, freq='h').values
        anyel = np.zeros(len(grid), bool)
        for s in live:
            m = pd.Series(data[s]['elig'], index=data[s]['t']).reindex(grid).fillna(False).values.astype(bool); anyel |= m
        elig_weeks = anyel.sum() / 168
        res['arms'][arm] = dict(status='INCOMPLETE' if any(s in excluded for s in syms) else 'COMPLETE',
                                eligible_weeks=round(float(elig_weeks), 2), orders=[list(o) for o in orders], costs={})
        for cb in (7, 15, 23):
            rows = []
            for k, od in enumerate(orders):
                tr, sk = simulate(data, syms, od, cb)
                sm = summarize(tr, sk, weeks, elig_weeks); sm['order'] = k; rows.append(sm)
                if k == 0:
                    pd.DataFrame([{kk: v for kk, v in t.items() if kk != 'marks'} for t in tr]).to_csv(
                        os.path.join(out, f'{arm}.{cb}bp.order0.trades.csv'), index=False)
            m = pd.DataFrame([{k: v for k, v in r.items() if not isinstance(v, dict)} for r in rows])
            q = lambda col: dict(median=round(float(m[col].median()), 4), p10=round(float(m[col].quantile(.1)), 4),
                                 p90=round(float(m[col].quantile(.9)), 4), min=round(float(m[col].min()), 4),
                                 max=round(float(m[col].max()), 4)) if col in m and m[col].notna().any() else None
            res['arms'][arm]['costs'][f'{cb}bp'] = dict(order0=rows[0], across_orders={c: q(c) for c in (
                'closes', 'closes_per_cal_week', 'closes_per_elig_week', 'meanR_budget', 'meanR_actual', 'early_meanR',
                'later_meanR', 'exbest_meanR', 'closedR_maxdd', 'marked_dd_R', 'longest_underwater_days')},
                share_orders_meanR_pos=round(float((m.meanR_budget > 0).mean()), 3))
            json.dump(res, open(os.path.join(out, 'd01v2_result.json'), 'w', encoding='utf-8'), indent=1, default=str)
    print('done')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
