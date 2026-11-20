"""BASKET-OLD3-H5 shared-account comparison (research only). Contract: Codex GO PR #337 6832559586 / #219 6832557765,
amending Claude draft 4fc01366. Frozen before any outcome. No account, runtime, key or engine change; master engine files
are imported unchanged (backtest fdab284c, strategies 0d416c8c, feasibility b689ecc4, prot 02955ce4).

Pipeline
1. Data: canonical cohort9_1h (manifest c11ed256). 4h bars = complete epoch-aligned groups of four 1h rows.
   All rows at or after END are dropped before anything is computed (no price use beyond the end).
2. Native tapes: backtest.run per sleeve standalone, flat at START, max_pos 9 (one lot per coin), no daily halt, no risk
   rules, exchange_rules snapshot d1ef2a9c. Sizing patched to the research rule ONLY: qty from fixed $8 pre-fee stop risk
   (feasibility.risk_qty with risk_usd=8), capped at $3,333.33 / entry fill, then the native step floor / minimum checks.
   Partials, dust, blocked partials, stops and exits are the native engine's at that actual quantity. Costs per run:
   SLIP 2 bp, FEE (c-2) bp, native funding 1 bp/8h per bar (1h: 1.25e-5, 4h: 5e-5) on qty x bar close.
3. Target ledger per lot from the native fills at native quantities: entry/exit slip and fee on executed qty; funding
   proxy 1 bp/8h per held hour on open qty x canonical 1h close (separately reconciled vs native funding).
4. Shared-account replay ($4,000, fixed $8 entry risk, no compounding) for three books x 40 tie orders.
"""
import math, random, json, hashlib, os, sys, heapq
from collections import defaultdict
from datetime import timezone
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import backtest as B, strategies as S, feasibility as F
import h5_hourly_long_pr338 as H5

COINS = ['ADAUSDT', 'AVAXUSDT', 'BNBUSDT', 'BTCUSDT', 'DOGEUSDT', 'ETHUSDT', 'LINKUSDT', 'SOLUSDT', 'XRPUSDT']
START = pd.Timestamp('2024-11-17 12:00')          # first bar processed on both TFs (flat); UTC naive
END = pd.Timestamp('2026-09-28 00:00')            # exclusive
SPLIT = pd.Timestamp('2025-10-01 00:00')          # primary periods, on entry
HALF = START + (END - START) / 2                  # secondary chronological halves, on entry (2025-10-22 18:00)
WARMUP = 220
RISK_USD, CAP_NOTIONAL, CAPITAL = 8.0, 3333.33, 4000.0
OPEN_RISK_PCT, DAY_HALT_PCT, DD_REPORT = 0.025, 0.03, 0.10
SLIP = 0.0002
FUND_H = 0.0001 / 8                               # per hour of holding
COSTS = (7, 15, 23)
PATHS = ('path', 'worst')
SLEEVES = [  # name, key, sides, tf, capacity
    ('BEAR', 'bear_breakdown', 'short', '4h', 2),
    ('EMA', 'ema_st', 'long', '4h', 2),
    ('HOT', 'hot_coin', 'long', '4h', 2),
    ('H5', H5.KEY, 'long', '1h', 1),
]
BOOKS = {'OLD3': ['BEAR', 'EMA', 'HOT'], 'H5': ['H5'], 'COMBINED': ['BEAR', 'EMA', 'HOT', 'H5']}
N_ORDERS = 40
CAIRO = ZoneInfo('Africa/Cairo')
H1, H4 = pd.Timedelta(hours=1), pd.Timedelta(hours=4)


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


# ---------------------------------------------------------------- data
def load_1h(folder):
    out = {}
    for s in COINS:
        d = pd.read_csv(os.path.join(folder, f'{s}.csv'))
        t = d['t']
        d['t'] = pd.to_datetime(t, unit='ms') if pd.api.types.is_numeric_dtype(t) else pd.to_datetime(t)
        d = d[['t', 'o', 'h', 'l', 'c', 'v', 'qv']].astype({k: float for k in 'ohlcv'} | {'qv': float})
        if d.t.duplicated().any(): raise ValueError(f'{s}: duplicate hours')
        out[s] = d[d.t < END].sort_values('t').reset_index(drop=True)
    return out


def to_4h(d1):
    """Complete epoch-aligned 4h groups only (exactly the four 1h rows t, t+1h, t+2h, t+3h)."""
    g = d1.assign(k=d1.t.dt.floor('4h'))
    n = g.groupby('k').t.transform('size')
    g = g[n == 4]
    a = g.groupby('k').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), v=('v', 'sum'), qv=('qv', 'sum'))
    return a.reset_index().rename(columns={'k': 't'})


def check_window(raw1, raw4):
    """Warmup >= 220 bars before START on each TF and START present on both; returns calendar facts."""
    facts = {}
    for tf, raw in (('1h', raw1), ('4h', raw4)):
        for s, d in raw.items():
            i = d.index[d.t == START]
            if not len(i): raise ValueError(f'{tf} {s}: START bar missing')
            if i[0] < WARMUP: raise ValueError(f'{tf} {s}: only {i[0]} warmup bars before START')
        facts[tf] = dict(first_bar=str(min(d.t.iloc[0] for d in raw.values())),
                         last_bar=str(max(d.t.iloc[-1] for d in raw.values())),
                         start_index=int(raw['BTCUSDT'].index[raw['BTCUSDT'].t == START][0]))
    return facts


# ---------------------------------------------------------------- native tapes
def register_h5():
    if H5.KEY not in S.STRATEGIES:
        S.STRATEGIES[H5.KEY] = dict(H5.RESEARCH, params=dict(H5.RESEARCH['params']), mgmt=dict(H5.RESEARCH['mgmt']))


class fixed_sizing:
    """Context manager: feasibility.risk_qty sized from fixed $8 and capped at $3,333.33 / fill. Nothing else changes."""
    def __enter__(self):
        self.orig = F.risk_qty
        def rq(m, risk_usd, px, atr, sd):
            if 'dca' in m: raise ValueError('DCA not expected in this study')
            z = self.orig(m, RISK_USD, px, atr, sd)
            z = dict(z); z['qty'] = min(z['qty'], CAP_NOTIONAL / px)
            return z
        F.risk_qty = rq
        return self

    def __exit__(self, *a):
        F.risk_qty = self.orig


def native_tape(raw, sleeve, cost_bp, path, rules, tf):
    """One standalone native run. Returns (lots, feas, book_t)."""
    name, key, sides, _tf, _cap = sleeve
    book = B.Book(raw)
    fpb = FUND_H * (1 if tf == '1h' else 4)
    fills = []
    B.FEE, B.SLIP = (cost_bp - 2) / 1e4, SLIP
    with fixed_sizing():
        tr, cv = B.run(book, [dict(key=key, share=1.0, risk=RISK_USD / 1e6, max_pos=9, sides=sides)], start=1e6,
                       max_lev=10.0, daily_halt=1.0, t0=START, t1=END, warmup=WARMUP, fund_per_bar=fpb,
                       pessimistic=path, exchange_rules=rules, fill_log=fills)
    T = pd.to_datetime(book.t.values)
    bar = H1 if tf == '1h' else H4
    lots = build_lots(name, fills, tr, T, bar, book, fpb, cost_bp)
    return lots, cv.attrs['feasibility'], T


def build_lots(name, fills, tr, T, bar, book, fpb, cost_bp):
    """Group native fills into lots; attach native P&L for closed lots and the native funding reconstruction."""
    by = defaultdict(list)
    for f in fills: by[(f['sym'], f['pos_i'])].append(f)
    closed = {} if tr is None or not len(tr) else {(r.sym, int(r.i_in)): r for r in tr.itertuples()}
    fee = (cost_bp - 2) / 1e4
    lots = []
    for (sym, i_in), fs in sorted(by.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        e = [f for f in fs if f['kind'] == 'entry']
        if len(e) != 1 or any(f['kind'] not in ('entry', 'exit') for f in fs): raise ValueError(f'unexpected fills {name} {sym} {i_in}')
        e = e[0]; side = e['side']; q0 = e['qty']
        ex = [dict(i=f['i'], ref=f['ref'], qty=f['qty'], why=f['why'], gap=f['gap']) for f in fs if f['kind'] == 'exit']
        q_exit = sum(x['qty'] for x in ex)
        is_closed = (sym, i_in) in closed
        if is_closed and abs(q_exit - q0) > 1e-9 * max(1, q0): raise ValueError(f'exit qty mismatch {name} {sym} {i_in}')
        i_out = closed[(sym, i_in)].i_out if is_closed else len(T) - 1
        # native funding: every bar i_in..i_out on the qty held at that bar's start, at that bar's close
        c = book.arr[sym]['c']; q = q0; fund = 0.0; ex_by_i = defaultdict(float)
        for x in ex: ex_by_i[x['i']] += x['qty']
        for i in range(i_in, i_out + 1):
            fund += q * c[i] * fpb
            q -= ex_by_i.get(i, 0.0)
        e_fill = e['ref'] * (1 + side * SLIP)
        cash = -q0 * e_fill * fee
        for x in ex:
            xf = x['ref'] * (1 - side * SLIP); cash += side * (xf - e_fill) * x['qty'] - x['qty'] * xf * fee
        lot = dict(sleeve=name, sym=sym, side=side, i_in=i_in, t_in=T[i_in], ref_in=e['ref'], e_fill=e_fill, qty=q0,
                   stop_dist=e['stop_dist'], risk_usd=q0 * e['stop_dist'], exits=[dict(x, t=T[x['i']] + bar) for x in ex],
                   closed=is_closed, t_out=(T[i_out] + bar) if is_closed else END, native_funding=fund,
                   native_fills_cash=cash, native_pnl=float(closed[(sym, i_in)].pnl) if is_closed else None,
                   id=f'{name}|{sym}|{T[i_in].isoformat()}')
        if is_closed and not math.isclose(cash - fund, lot['native_pnl'], rel_tol=1e-8, abs_tol=1e-8):
            raise ValueError(f'native identity failed {lot["id"]}: {cash - fund} vs {lot["native_pnl"]}')
        lots.append(lot)
    return lots


# ---------------------------------------------------------------- target ledger
def target_cash_events(lot, closes1h, cost_bp):
    """Cash events of one lot at native quantities: entry fee at t_in, each exit fill at its bar end, hourly funding
    proxy at each held hour's end on the qty open during that hour. Returns list of (t, kind, amount, qty_after)."""
    fee = (cost_bp - 2) / 1e4; side = lot['side']; e = lot['e_fill']; q = lot['qty']
    ev = [(lot['t_in'], 'entry_fee', -q * e * fee)]
    exits = sorted(lot['exits'], key=lambda x: x['t'])
    t_end = lot['t_out']
    c = closes1h[lot['sym']]
    k = 0; h = lot['t_in']
    while h < t_end:
        while k < len(exits) and exits[k]['t'] <= h:                     # exits at bar end <= this hour start
            x = exits[k]; xf = x['ref'] * (1 - side * SLIP)
            ev.append((x['t'], 'exit', side * (xf - e) * x['qty'] - x['qty'] * xf * fee)); q -= x['qty']; k += 1
        if q > 1e-12:
            px = c.get(h)
            if px is None: raise ValueError(f'missing 1h close {lot["sym"]} {h}')
            ev.append((h + H1, 'funding', -q * px * FUND_H))
        h += H1
    for x in exits[k:]:
        xf = x['ref'] * (1 - side * SLIP)
        ev.append((x['t'], 'exit', side * (xf - e) * x['qty'] - x['qty'] * xf * fee))
    return ev


# ---------------------------------------------------------------- replay
def orders(seed):
    sl = [s[0] for s in SLEEVES]; co = list(COINS)
    if seed:
        r = random.Random(seed); r.shuffle(sl); r.shuffle(co)
    return sl, co


def day_of(t):
    return pd.Timestamp(t).tz_localize('UTC').tz_convert(CAIRO).date()


def replay(lots, book, seed, closes1h, cost_bp, hours):
    """Deterministic shared-account replay. lots: all candidate lots of the book's sleeves (one cost/path)."""
    sl_order, co_order = orders(seed)
    sl_order = [s for s in sl_order if s in BOOKS[book]]
    srank = {s: i for i, s in enumerate(sl_order)}; crank = {c: i for i, c in enumerate(co_order)}
    cap = {s[0]: s[4] for s in SLEEVES}
    cand = [l for l in lots if l['sleeve'] in srank]
    ev = []
    for l in cand:
        ev.append((l['t_in'], 2, srank[l['sleeve']], crank[l['sym']], l['id'], 'cand', l))
        if l['closed']: ev.append((l['t_out'], 0, srank[l['sleeve']], crank[l['sym']], l['id'], 'release', l))
    for h in hours:                                       # hourly mark at hour end: after releases, before new entries
        ev.append((h + H1, 1, 0, 0, '', 'mark', h))
    ev.sort(key=lambda e: (e[0], e[1], e[2], e[3], e[4]))
    cash = CAPITAL; active = {}; phantom = {}
    blocked = defaultdict(int); phantom_hours = 0.0; recs = {}
    day = None; day_start = cash; day_real = 0.0; halt = False
    peak_m = CAPITAL; mdd = 0.0; first10 = None; peak_c = CAPITAL; cdd = 0.0
    risk_hours = 0.0; peak_risk = 0.0; gross_hours = 0.0; peak_gross = 0.0; util_hours = 0.0; n_hours = 0
    cash_ev = []                                          # heap of (t, seq, amount, lot_id, kind) from admitted lots
    seq = 0

    def settle(upto, inclusive):
        nonlocal cash, day, day_start, day_real, halt, peak_c, cdd
        while cash_ev and (cash_ev[0][0] < upto or (inclusive and cash_ev[0][0] == upto)):
            t, _, amt, lid, kind = heapq.heappop(cash_ev)
            roll(t)
            cash += amt; day_real += amt; recs[lid]['net'] += amt
            if kind == 'funding': recs[lid]['funding'] += amt
            if day_real <= -DAY_HALT_PCT * day_start: halt = True
            peak_c = max(peak_c, cash); cdd = max(cdd, (peak_c - cash) / peak_c)

    def roll(t):
        nonlocal day, day_start, day_real, halt
        d = day_of(t)
        if d != day: day, day_start, day_real, halt = d, cash, 0.0, False

    for t, pri, _, _, lid, kind, obj in ev:
        if kind == 'release':
            settle(t, True)
            active.pop(lid, None); phantom.pop(lid, None)
            continue
        if kind == 'mark':
            settle(t, True)
            h = obj; up = 0.0; risk = 0.0; gross = 0.0
            for a in active.values():
                l = a['lot']; q = a['qty_at'](h + H1)
                px = closes1h[l['sym']][h]
                up += l['side'] * (px - l['e_fill']) * q; risk += l['risk_usd']; gross += q * px
            m = cash + up
            peak_m = max(peak_m, m); dd = (peak_m - m) / peak_m; mdd = max(mdd, dd)
            if first10 is None and dd >= DD_REPORT: first10 = (h + H1).isoformat()
            risk_hours += risk; peak_risk = max(peak_risk, risk); gross_hours += gross; peak_gross = max(peak_gross, gross)
            util_hours += risk / (OPEN_RISK_PCT * cash) if cash > 0 else 0.0; n_hours += 1
            continue
        # candidate entry at t: settle all cash strictly before t and at t (exits/partials/funding at t come first)
        settle(t, True); roll(t)
        l = obj; s = l['sleeve']
        reason = ''
        if cash <= 0: reason = 'nonpositive_equity'
        elif halt: reason = 'cairo_daily_loss_halt'
        elif sum(a['lot']['sleeve'] == s for a in active.values()) >= cap[s]: reason = 'sleeve_position_cap'
        elif any(a['lot']['sleeve'] == s and a['lot']['sym'] == l['sym'] for a in active.values()): reason = 'own_coin_slot'
        elif sum(a['lot']['risk_usd'] for a in active.values()) + l['risk_usd'] > OPEN_RISK_PCT * cash + 1e-9:
            reason = 'aggregate_entry_risk_cap'
        recs[lid] = dict(id=lid, sleeve=s, sym=l['sym'], side=l['side'], t_in=l['t_in'], t_out=l['t_out'],
                         status='refused' if reason else 'admitted', reason=reason, net=0.0, funding=0.0,
                         closed=l['closed'], risk=l['risk_usd'] if not reason else 0.0,
                         notional=l['qty'] * l['e_fill'] if not reason else 0.0)
        if reason:
            blocked[reason] += 1; phantom[lid] = l
            phantom_hours += (min(l['t_out'], END) - l['t_in']) / H1
            continue
        ev_l = target_cash_events(l, closes1h, cost_bp)
        for (tc, k2, amt) in ev_l:
            seq += 1; heapq.heappush(cash_ev, (tc, seq, amt, lid, k2))
        qty_steps = sorted((x['t'], x['qty']) for x in l['exits'])
        def qty_at(h, l=l, qty_steps=qty_steps):
            q = l['qty']
            for tx, qx in qty_steps:
                if tx <= h: q -= qx
            return max(q, 0.0)
        active[lid] = dict(lot=l, qty_at=qty_at)
        settle(t, True)                                    # entry fee at t
    settle(END, True)
    # terminal mark: open admitted lots at the final available close (time END)
    term_up = 0.0; last_h = END - H1
    for a in active.values():
        l = a['lot']; q = a['qty_at'](last_h + H1)
        term_up += l['side'] * (closes1h[l['sym']][last_h] - l['e_fill']) * q
    return summarize(recs, cash, term_up, mdd, first10, cdd, blocked, phantom_hours, risk_hours, peak_risk,
                     gross_hours, peak_gross, util_hours, n_hours)


def summarize(recs, cash, term_up, mdd, first10, cdd, blocked, phantom_hours, risk_hours, peak_risk, gross_hours,
              peak_gross, util_hours, n_hours):
    adm = [r for r in recs.values() if r['status'] == 'admitted']
    term = cash + term_up
    weeks = (END - START) / pd.Timedelta(days=7)
    pnl = term - CAPITAL
    lot_pnl = sorted((r['net'] for r in adm), reverse=True)
    by = lambda k: {g: sum(r['net'] for r in adm if r[k] == g) for g in sorted({r[k] for r in adm})}
    coin, sleeve = by('sym'), by('sleeve')
    period = lambda a, b: sum(r['net'] for r in adm if a <= r['t_in'] < b)
    return dict(
        terminal_marked_equity=term, net_usd=pnl, net_pct=pnl / CAPITAL * 100, closed_cash=cash, open_mark=term_up,
        closes=sum(r['closed'] for r in adm), admitted=len(adm), entries_per_week=len(adm) / weeks,
        marked_dd_pct=mdd * 100, closed_dd_pct=cdd * 100, first_10pct_marked_dd=first10,
        blocked=dict(blocked), refused=sum(blocked.values()), phantom_hours=phantom_hours,
        funding_usd=sum(r['funding'] for r in adm),
        early_usd=period(START, SPLIT), later_usd=period(SPLIT, END),
        half1_usd=period(START, HALF), half2_usd=period(HALF, END),
        by_coin=coin, by_sleeve=sleeve,
        top_coin_share=(max(coin.values()) / pnl) if coin and pnl > 0 else None,
        top_sleeve_share=(max(sleeve.values()) / pnl) if sleeve and pnl > 0 else None,
        best_lot_removed_usd=pnl - (lot_pnl[0] if lot_pnl else 0.0),
        avg_open_risk=risk_hours / n_hours if n_hours else 0.0, peak_open_risk=peak_risk,
        avg_gross_notional=gross_hours / n_hours if n_hours else 0.0, peak_gross_notional=peak_gross,
        peak_gross_leverage=peak_gross / CAPITAL, avg_risk_utilization=util_hours / n_hours if n_hours else 0.0,
        return_over_marked_dd=(pnl / CAPITAL * 100) / (mdd * 100) if mdd > 0 else None,
        records=[{k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in r.items()} for r in recs.values()])


def lead_label(cells):
    """PORTFOLIO_RESEARCH_LEAD iff: 15bp both paths combined > EACH baseline in >= 30/40 paired orders; combined 15bp
    positive in both primary periods (all orders, both paths); combined 23bp net positive (all orders, both paths)."""
    ok = True; detail = {}
    for path in PATHS:
        for base in ('OLD3', 'H5'):
            w = sum(cells[('COMBINED', 15, path, s)]['net_usd'] > cells[(base, 15, path, s)]['net_usd'] for s in range(N_ORDERS))
            detail[f'15bp_{path}_beats_{base}'] = w; ok &= w >= 30
        e = all(cells[('COMBINED', 15, path, s)]['early_usd'] > 0 and cells[('COMBINED', 15, path, s)]['later_usd'] > 0
                for s in range(N_ORDERS))
        p23 = all(cells[('COMBINED', 23, path, s)]['net_usd'] > 0 for s in range(N_ORDERS))
        detail[f'15bp_{path}_both_periods_all_orders'] = e; detail[f'23bp_{path}_positive_all_orders'] = p23
        ok &= e and p23
    return ('PORTFOLIO_RESEARCH_LEAD' if ok else 'NOT_A_LEAD'), detail
