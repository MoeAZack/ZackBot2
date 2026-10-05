"""Engine-vs-backtest replay, as a reusable function.

The LIVE engine (engine.Engine, unchanged) is driven candle by candle against a simulated Binance; the backtester runs the
same slots on the same candles. Every closed position is matched by (strategy, coin, side, entry candle) and compared.

Intrabar path = the backtester's convention (green open->low->high->close, red open->high->low->close), walked in `steps`
small moves per leg so adds/stops trigger near their level, as the live bot does with its 8-second price checks.

Metrics (release targets proposed by the independent reviewer, 2026-10-05):
  matched trades >= 98 %, median |dR| <= 0.05, p95 |dR| <= 0.25, total-return gap <= 3 pp, max-DD gap <= 2 pp,
  zero position mismatches (exchange vs engine, dust excluded), one exchange stop per open lot.
"""
import os, tempfile, types, logging
import numpy as np, pandas as pd
import backtest as B
import engine as E

STRICT = dict(matched_pct=98.0, med_dr=0.05, p95_dr=0.25, ret_gap=3.0, dd_gap=2.0)
LOOSE_RET_GAP = 15.0          # interim hard gate while the shared-core refactor is pending


def run_replay(raw, sleeves, t0, steps=6, start=500.0, tf_sec=14400, quiet=True, stop_at=None):
    """raw: {symbol: DataFrame t,o,h,l,c,v} (same length / timestamps); sleeves: engine slot dicts; t0: first bar index.
    Returns dict(engine_curve, bt_curve, engine_trades, bt_trades, matched, metrics, mismatch, stops, lots).
    stop_at=(bar, leg, step) or a list of them: stop INSIDE that candle's price path (leg 0-2, step 1..steps) and return only the engine's
    decisions so far: dict(lots, events, history, stops) (a list -> {point: that dict}) - used by the causality test."""
    syms = list(raw)
    D = {s: raw[s].reset_index(drop=True) for s in syms}
    N = len(D[syms[0]])
    FEE, SLIP = B.FEE, B.SLIP

    class W:
        i = None; mark = {}; cash = start; lots = []; stops = {}; n = 0

    def merge(s, ps):
        ls = [l for l in W.lots if l['s'] == s and l['ps'] == ps]
        if len(ls) > 1:
            q = sum(l['q'] for l in ls); avg = sum(l['q'] * l['avg'] for l in ls) / q
            for l in ls: W.lots.remove(l)
            W.lots.append(dict(s=s, ps=ps, q=q, avg=avg))

    def reduce(s, ps, q, px):
        l = next(l for l in W.lots if l['s'] == s and l['ps'] == ps)
        q = min(q, l['q']); sd = 1 if ps == 'LONG' else -1
        W.cash += sd * (px - l['avg']) * q - q * px * FEE; l['q'] -= q
        if l['q'] <= 1e-12: W.lots.remove(l)

    def equity():
        return W.cash + sum((1 if l['ps'] == 'LONG' else -1) * (W.mark[l['s']] - l['avg']) * l['q'] for l in W.lots)

    def move(px_by_sym):
        for s, p in px_by_sym.items():
            W.mark[s] = p
            for tag, st in list(W.stops.items()):
                if st['s'] != s: continue
                sd = 1 if st['ps'] == 'LONG' else -1
                if sd * (p - st['p']) <= 0:
                    have = sum(l['q'] for l in W.lots if l['s'] == s and l['ps'] == st['ps'])
                    q = min(st['q'], have)
                    if q > 0: reduce(s, st['ps'], q, st['p'] * (1 - sd * SLIP))
                    W.stops.pop(tag)

    class Fake:
        def __init__(self, *a, **k): pass
        def sync_time(self): pass
        def exchange_info(self):
            return {'symbols': [{'symbol': s, 'contractType': 'PERPETUAL', 'status': 'TRADING', 'filters': [
                {'filterType': 'MARKET_LOT_SIZE', 'stepSize': '0.00001', 'minQty': '0.00001'}, {'filterType': 'PRICE_FILTER', 'tickSize': '0.000001'},
                {'filterType': 'MIN_NOTIONAL', 'notional': str(B.MIN_NOTIONAL.get(s, 5))}]} for s in syms]}
        def klines(self, s, tf, limit=1500, **k):
            d = D[s].iloc[max(0, W.i - 1000 + 1):W.i + 2]
            ms = (d.t.astype('datetime64[ns]').astype('int64') // 10 ** 6).values
            return [[int(t), o, h, l, c, v, int(t) + tf_sec * 1000 - 1] for t, o, h, l, c, v in zip(ms, d.o, d.h, d.l, d.c, d.v)]
        def marks(self): return dict(W.mark)
        def premium(self, s): return {'lastFundingRate': '0.0001'}
        def account(self): return {'totalMarginBalance': str(equity())}
        def positions(self):
            out = {}
            for l in W.lots: out[(l['s'], l['ps'])] = out.get((l['s'], l['ps']), 0) + l['q']
            return {k: v for k, v in out.items() if v > 1e-12}
        def hedge_mode(self): return True
        def set_hedge_mode(self, on=True): pass
        def set_leverage(self, *a): pass
        def set_margin_type(self, *a): pass
        def open(self, s, ps, qty):
            q = float(qty); sd = 1 if ps == 'LONG' else -1; px = W.mark[s] * (1 + sd * SLIP)
            W.cash -= q * px * FEE; W.lots.append(dict(s=s, ps=ps, q=q, avg=px)); merge(s, ps)
            return {'avgPrice': str(px)}
        def close(self, s, ps, qty):
            q = float(qty); sd = 1 if ps == 'LONG' else -1; px = W.mark[s] * (1 - sd * SLIP)
            reduce(s, ps, q, px); return {'avgPrice': str(px)}
        def stop(self, s, ps, qty, price):
            W.n += 1; tag = f'o:{W.n}'; W.stops[tag] = dict(s=s, ps=ps, q=float(qty), p=float(price)); return tag
        def cancel(self, s, tag): W.stops.pop(tag, None)
        def open_stop_tags(self, s): return {k for k, v in W.stops.items() if v['s'] == s}
        def leverage_max(self, s): return 50
        def cancel_all(self, s):
            for k in [k for k, v in W.stops.items() if v['s'] == s]: W.stops.pop(k)

    saved = (E.Futures, E.now_utc, E.time)
    lg = logging.getLogger('zackbot'); lvl = lg.level
    if quiet: lg.setLevel(logging.WARNING)
    fake_now = [0.0]
    stop_set = ({stop_at} if isinstance(stop_at, tuple) else set(stop_at)) if stop_at else set()
    snaps = {}
    try:
        E.Futures = Fake
        E.now_utc = lambda: pd.Timestamp(fake_now[0], unit='s', tz='UTC').to_pydatetime()
        E.time = types.SimpleNamespace(time=lambda: fake_now[0], sleep=lambda x: None)
        tmp = tempfile.mkdtemp(prefix='zb_replay_')
        eng = E.Engine(dict(MODE='paper', API_KEY='x', API_SECRET='y'), tmp)
        eng.S['SLEEVES'] = sleeves; eng.S['UNIVERSE'] = syms; eng.S['SYMBOLS_ON'] = {s: True for s in syms}
        eng.S['CAPITAL_CAP'] = 0; eng.S['MAX_LEVERAGE'] = 10
        eng.equity_hist = []; eng.record_equity = lambda e: None
        eng.connect()
        tf = next((k for k, v in E.TF_SEC.items() if v == tf_sec), '4h')
        curve = []
        for i in range(t0, N - 1):
            W.i = i
            fake_now[0] = D[syms[0]].t[i].timestamp() + tf_sec + 15
            W.mark.update({s: D[s].o[i + 1] for s in syms})
            eng.cycle(tf)
            j = i + 1
            legs = [{s: (D[s].l[j] if D[s].c[j] >= D[s].o[j] else D[s].h[j]) for s in syms},
                    {s: (D[s].h[j] if D[s].c[j] >= D[s].o[j] else D[s].l[j]) for s in syms},
                    {s: D[s].c[j] for s in syms}]
            for li, tgt in enumerate(legs):
                a = dict(W.mark)
                for k in range(1, steps + 1):
                    move({s: a[s] + (tgt[s] - a[s]) * k / steps for s in syms})
                    if eng.state['lots']: eng.manage(dict(W.mark))
                    if stop_at and (j, li, k) in stop_set:
                        import copy as _c, csv as _csv
                        f = os.path.join(tmp, 'trades.csv')
                        ev = list(_csv.DictReader(open(f))) if os.path.exists(f) else []
                        snaps[(j, li, k)] = dict(lots=_c.deepcopy(eng.state['lots']), events=ev, history=_c.deepcopy(eng.history),
                                                 stops=_c.deepcopy(W.stops), cash=W.cash)
                        if len(snaps) == len(stop_set):
                            return snaps[stop_at] if isinstance(stop_at, tuple) else snaps
                if not eng.state['lots']: eng.manage(dict(W.mark))
            W.cash -= sum(l['q'] * W.mark[l['s']] * B.FUND_PER_BAR * tf_sec / 14400 for l in W.lots)
            curve.append((D[syms[0]].t[j], equity()))
    finally:
        E.Futures, E.now_utc, E.time = saved
        lg.setLevel(lvl)
    cv = pd.Series([c for _, c in curve], index=[t for t, _ in curve])
    bk = B.Book({s: raw[s] for s in syms})
    def order(sl):   # same coin order as the live engine (sleeve_symbols) and the app's backtests: first signal in this order wins a free slot
        base = E.CORE8 if sl['symbols'] == 'core8' else syms if sl['symbols'] == 'all' else sl['symbols']
        return [x for x in base if x in syms]
    cfg = [dict(key=s['key'], share=s['share'], risk=s['risk'], max_pos=s['max_pos'], symbols=order(s), sides=s['sides'], mgmt=s['mgmt'],
                **{k: s[k] for k in ('when', 'trail_entry', 'pump_guard') if s.get(k) is not None}) for s in sleeves]
    t2, c2 = B.run(bk, cfg, start=start, max_lev=10, t0=D[syms[0]].t[t0 + 1], fund_per_bar=B.FUND_PER_BAR * tf_sec / 14400)

    # ---- trade matching
    t_index = {ts: k for k, ts in enumerate(D[syms[0]].t)}
    key_of = {s['id']: s['key'] for s in sleeves}
    eh = pd.DataFrame(eng.history)
    if len(eh):
        def bar(ts): return t_index.get(pd.Timestamp(ts).tz_localize(None).floor(f'{tf_sec}s'))
        eh = pd.DataFrame(dict(key=eh.sleeve.map(key_of), sym=eh.symbol, side=np.where(eh.side == 'LONG', 1, -1),
                               i_in=eh.opened.map(bar), R=eh.pnl / eh.risk_usd, pnl=eh.pnl))
    else:
        eh = pd.DataFrame(columns=['key', 'sym', 'side', 'i_in', 'R', 'pnl'])
    bt = t2.rename(columns={'sleeve': 'key'})[['key', 'sym', 'side', 'i_in', 'R', 'pnl']] if len(t2) else pd.DataFrame(columns=['key', 'sym', 'side', 'i_in', 'R', 'pnl'])
    m = eh.merge(bt, on=['key', 'sym', 'side', 'i_in'], how='outer', suffixes=('_e', '_b'), indicator=True)
    both = m[m._merge == 'both']
    dR = (both.R_e - both.R_b).abs()
    n_all = max(len(eh), len(bt), 1)
    # ---- exchange vs engine
    held = {}
    for l in W.lots: held[(l['s'], l['ps'])] = held.get((l['s'], l['ps']), 0.0) + l['q']
    mine = {}
    for l in eng.state['lots'].values(): mine[(l['symbol'], l['side'])] = mine.get((l['symbol'], l['side']), 0.0) + l['qty']
    dust = lambda k, q: q * W.mark[k[0]] < eng.rules.get(k[0], {}).get('min_notional', 5)
    mismatch = {f'{k[0]} {k[1]}': (held.get(k, 0), mine.get(k, 0)) for k in set(held) | set(mine) if not dust(k, abs(held.get(k, 0) - mine.get(k, 0)))}
    ret = lambda c: (c.iloc[-1] / start - 1) * 100
    mdd = lambda c: (c / c.cummax() - 1).min() * 100
    metrics = dict(engine_ret=round(ret(cv), 2), bt_ret=round(ret(c2), 2), ret_gap=round(abs(ret(cv) - ret(c2)), 2),
                   engine_dd=round(mdd(cv), 2), bt_dd=round(mdd(c2), 2), dd_gap=round(abs(mdd(cv) - mdd(c2)), 2),
                   trades_engine=len(eh), trades_bt=len(bt), matched=len(both), matched_pct=round(len(both) / n_all * 100, 1),
                   med_dr=round(float(dR.median()), 3) if len(dR) else 0.0, p95_dr=round(float(dR.quantile(.95)), 3) if len(dR) else 0.0,
                   mismatches=len(mismatch), stops=len(W.stops), lots=len(eng.state['lots']))
    metrics['strict_fail'] = [k for k, lim in STRICT.items() if (metrics[k] < lim if k == 'matched_pct' else metrics[k] > lim)]
    metrics['hard_ok'] = metrics['ret_gap'] <= LOOSE_RET_GAP and not mismatch and len(W.stops) == len(eng.state['lots'])
    return dict(engine_curve=cv, bt_curve=c2, engine_trades=eh, bt_trades=bt, matched=m, metrics=metrics, mismatch=mismatch,
                history=eng.history, missed=eng.missed)
