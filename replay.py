"""Engine-vs-backtest replay, as a reusable function.

The LIVE engine (engine.Engine, unchanged) is driven candle by candle against a simulated Binance; the backtester runs the
same slots on the same candles. Every closed position is matched by (strategy, coin, side, entry candle) and compared.

Intrabar path = the backtester's convention (backtest.path_points: green open->low->high->close, red open->high->low->close,
doji = worst case for the side held on that coin), walked in `steps`
small moves per leg so adds/stops trigger near their level, as the live bot does with its 8-second price checks.

Metrics (release targets proposed by the independent reviewer, 2026-10-05):
  matched trades >= 98 %, median |dR| <= 0.05, p95 |dR| <= 0.25, total-return gap <= 3 pp, max-DD gap <= 2 pp,
  zero position mismatches (exchange vs engine, dust excluded), one exchange stop per open lot.
"""
import copy, os, tempfile, types, logging
import numpy as np, pandas as pd
import backtest as B
import engine as E
import feasibility as F

STRICT = dict(matched_pct=98.0, med_dr=0.05, p95_dr=0.25, ret_gap=3.0, dd_gap=2.0)
LOOSE_RET_GAP = 15.0          # interim hard gate while the shared-core refactor is pending


def qty_flat_tolerance(symbol, exchange_rules=None):
    """Largest sub-step remainder that the simulated exchange treats as flat.

    Keep this boundary shared with the execution-packet reconstruction.  A whole
    exchange step is real quantity; only a strictly smaller remainder is dust.
    """
    rules = F.snapshot_rules(exchange_rules)
    rule = (rules or {}).get(symbol) or {}
    step = float(rule.get('step') or 1e-5)
    return max(1e-12, 0.99 * step)


class SyncEnricher:
    """B1 (SCALP-EXEC-15M-01): the engine's fill enrichment run inline - the same enrich_attempt / enrich_finish, at most
    ENRICH_ATTEMPTS reads, no thread and no clock - so a replay stays deterministic. The record goes only to the engine's
    cost queue (apply_costs); no fill file is written."""
    closing = False

    def submit(self, rec, refs, client, writer, sink=None):
        rec, refs, n = copy.deepcopy(rec), [dict(r) for r in refs], 0
        while n < E.ENRICH_ATTEMPTS:
            n += 1
            if not E.enrich_attempt(rec, refs, client): break
        E.enrich_finish(rec, refs, cut=False); rec['enrich_attempts'] = n
        if sink: sink(rec)
        return 'queued'

    def flush(self, timeout=2.0): return True
    def close(self, timeout=2.0): return True
    def summary(self): return {}


def run_replay(raw, sleeves, t0, steps=6, start=500.0, tf_sec=14400, quiet=True, stop_at=None, exchange_rules=None,
               path_fn=None, costs_off=False, fill_log=None, compare=True, pre_fn=None, fill_px=None, fund_fn=None,
               settings=None, measured_fills=False):
    """raw: {symbol: DataFrame t,o,h,l,c,v[,qv]} (same length / timestamps; qv = Binance kline quote volume, optional); sleeves: engine slot dicts; t0: first bar index.
    Returns dict(engine_curve, bt_curve, engine_trades, bt_trades, matched, metrics, mismatch, stops, lots).
    stop_at=(bar, leg, step) or a list of them: stop INSIDE that candle's price path (leg 0-2, step 1..steps) and return only the engine's
    decisions so far: dict(lots, events, history, stops) (a list -> {point: that dict}) - used by the causality test.
    exchange_rules (BT02): a snapshot or {symbol: rule}. The simulated exchange then serves exactly these filters to the engine
    and the backtester gets the same rules (both floor to the step and skip below the minimums through feasibility.size_check).
    None (default): unchanged - the engine sees step 1e-5 / backtest.MIN_NOTIONAL, the backtester its legacy floor.
    SCALP-EXEC-15M-01 research hooks (all off by default; off = the exact behaviour above):
      path_fn(j, held, lots) -> {symbol: [(price, at_open, t_ms), ...]}: the price points of candle j AFTER its open, walked one
                  point at a time (each point one manage() call when lots are open); at_open=True makes that point a gap open
                  (a stop it crosses fills there, like the candle open). A symbol missing from the dict keeps the legacy path.
                  held = {symbol: {+1 / -1 sides held}}; lots = the engine's open lots (read only). Not combinable
                  with stop_at.
      costs_off   the simulated exchange charges no fee, slippage or flat funding (fills at the reference price), so a
                  research cost model can re-price every fill from fill_log.
      fill_log    a list: every simulated fill is appended as dict(t_ms, sym, ps, act ('open' / 'close'), kind ('market' /
                  'stop' / 'gap'), qty, ref (price before slippage)); every stop order placed is logged as act
                  'stop_set' (ref = its price). Recording only.
      compare     False skips the backtester run and the trade matching (engine-only result).
      pre_fn(j, held, lots) -> points like path_fn, walked BEFORE the engine's cycle at candle j's open (decision latency:
                  the cycle then runs at the last point's price; open lots are managed on the way).
      fill_px(fill) -> (price, fee_usd): prices every simulated fill (fill = the fill_log dict) instead of the legacy
                  SLIP / FEE; the engine sees that price as the exchange's avgPrice (so post-fill checks act on it).
      fund_fn(t_from_ms, t_to_ms, lots) -> cash paid (+) / received (-) for the funding times in [t_from, t_to) of
                  the exchange lots held at the candle open T (after a gap stop at the open; with pre_fn before the delayed
                  cycle, else after it, so an entry AT T counts), replacing the legacy flat funding.
      settings    dict merged into the engine settings before connect (e.g. PRESET).
      measured_fills  B1 (SCALP-EXEC-15M-01, harness only): every simulated order answers with an order id and its
                  executed quantity, user_trades() returns its fill (price and fee as simulated), and the engine's fill
                  enrichment runs synchronously, so RISK-FILL-01 acts on the measured fill. Ids stay in memory. Off = B0,
                  the default: the simulated exchange is unchanged."""
    syms = list(raw)
    D = {s: raw[s].reset_index(drop=True) for s in syms}
    N = len(D[syms[0]])
    FEE, SLIP = (0.0, 0.0) if costs_off else (B.FEE, B.SLIP)
    if path_fn is not None and stop_at: raise ValueError('path_fn and stop_at cannot be combined')
    XR = F.snapshot_rules(exchange_rules)

    class W:
        i = None; mark = {}; cash = start; lots = []; stops = {}; n = 0; t_ms = None; last = None; trades = {}

    def log_fill(s, ps, act, kind, q, ref):
        f = dict(t_ms=W.t_ms, sym=s, ps=ps, act=act, kind=kind, qty=q, ref=ref)
        if fill_log is not None: fill_log.append(f)
        return f

    def priced(f, legacy_px):
        """(fill price, fee) for one fill: the research pricer when given, else the legacy constants."""
        if fill_px is None: px, fee = legacy_px, f['qty'] * legacy_px * FEE
        else:
            px, fee = fill_px(f)
            if fill_log is not None: f.update(px=px, fee=fee)
        W.last = (f['qty'], px, fee)
        return px, fee

    def merge(s, ps):
        ls = [l for l in W.lots if l['s'] == s and l['ps'] == ps]
        if len(ls) > 1:
            q = F.qty_sum(*(l['q'] for l in ls)); avg = sum(l['q'] * l['avg'] for l in ls) / q
            q_in = sum(l.get('q_in', l['q']) for l in ls)
            for l in ls: W.lots.remove(l)
            W.lots.append(dict(s=s, ps=ps, q=q, q_in=q_in, avg=avg))

    def reduce(s, ps, q, px, kind='market', ref=None):
        l = next((l for l in W.lots if l['s'] == s and l['ps'] == ps), None)
        if l is None:      # Binance rejects a close with no position; a bare StopIteration here logged an empty error
            raise RuntimeError(f'simulated exchange holds no {s} {ps} position to close ({q})')
        q = min(q, l['q']); sd = 1 if ps == 'LONG' else -1
        px, fee = priced(log_fill(s, ps, 'close', kind, q, px if ref is None else ref), px)
        W.cash += sd * (px - l['avg']) * q - fee; l['q'] = F.qty_sum(l['q'], -q)   # exact, as Binance holds it
        tol = qty_flat_tolerance(s, XR)
        if 1e-12 < l['q'] <= tol:
            lg.warning('replay sub-step remainder treated as flat: %s %s residual=%.17g tolerance=%.17g',
                       s, ps, l['q'], tol)
        if l['q'] <= tol: W.lots.remove(l)
        return px

    def equity():
        return W.cash + sum((1 if l['ps'] == 'LONG' else -1) * (W.mark[l['s']] - l['avg']) * l['q'] for l in W.lots)

    def move(px_by_sym, at_open=False):
        # at_open (AUD-07 C13f): the candle OPEN is a price move like any other, so a stop-market the open has gapped through
        # triggers there and fills at the open (where the market is), with the exit slippage - the backtester's convention
        # (backtest.py: gap at the open -> close at o). Inside the path the step lands near the level, so the stop price is kept.
        for s, p in px_by_sym.items():
            W.mark[s] = p
            for tag, st in list(W.stops.items()):
                if st['s'] != s: continue
                sd = 1 if st['ps'] == 'LONG' else -1
                if sd * (p - st['p']) <= 0:
                    have = sum(l['q'] for l in W.lots if l['s'] == s and l['ps'] == st['ps'])
                    q = min(st['q'], have)
                    ref = p if at_open else st['p']
                    if q > 0: reduce(s, st['ps'], q, ref * (1 - sd * SLIP), 'gap' if at_open else 'stop', ref)
                    W.stops.pop(tag)

    class Fake:
        def __init__(self, *a, **k): pass
        def sync_time(self): pass
        def exchange_info(self):
            if XR is not None:                 # a symbol missing from the rules keeps the default filters below (backtest: legacy floor)
                return F.exchange_info_from_rules({s: XR.get(s) or dict(step=1e-5, min_qty=1e-5, tick=1e-6, min_notional=B.MIN_NOTIONAL.get(s, 5))
                                                   for s in syms})
            return {'symbols': [{'symbol': s, 'contractType': 'PERPETUAL', 'status': 'TRADING', 'filters': [
                {'filterType': 'MARKET_LOT_SIZE', 'stepSize': '0.00001', 'minQty': '0.00001'}, {'filterType': 'PRICE_FILTER', 'tickSize': '0.000001'},
                {'filterType': 'MIN_NOTIONAL', 'notional': str(B.MIN_NOTIONAL.get(s, 5))}]} for s in syms]}
        def klines(self, s, tf, limit=1500, **k):
            d = D[s].iloc[max(0, W.i - 1000 + 1):W.i + 2]
            ms = (d.t.astype('datetime64[ns]').astype('int64') // 10 ** 6).values
            rows = [[int(t), o, h, l, c, v, int(t) + tf_sec * 1000 - 1] for t, o, h, l, c, v in zip(ms, d.o, d.h, d.l, d.c, d.v)]
            if 'qv' in d:      # VWAPREV: the real kline quote volume (field 7) when the source has it; absent -> engine NaN (fails closed)
                for r, q in zip(rows, d.qv): r.append(q)
            return rows
        def marks(self): return dict(W.mark)
        def premium(self, s): return {'lastFundingRate': '0.0001'}
        def account(self): return {'totalMarginBalance': str(equity())}
        def positions(self):
            out = {}
            for l in W.lots: out[(l['s'], l['ps'])] = out.get((l['s'], l['ps']), 0) + l['q']
            return {k: v for k, v in out.items() if v > qty_flat_tolerance(k[0], XR)}
        def hedge_mode(self): return True
        def set_hedge_mode(self, on=True): pass
        def set_leverage(self, *a): pass
        def set_margin_type(self, *a): pass
        def open(self, s, ps, qty):
            q = float(qty); sd = 1 if ps == 'LONG' else -1
            px, fee = priced(log_fill(s, ps, 'open', 'market', q, W.mark[s]), W.mark[s] * (1 + sd * SLIP))
            W.cash -= fee; W.lots.append(dict(s=s, ps=ps, q=q, q_in=q, avg=px)); merge(s, ps)
            return {'avgPrice': str(px)}
        def close(self, s, ps, qty):
            q = float(qty); sd = 1 if ps == 'LONG' else -1; px = W.mark[s] * (1 - sd * SLIP)
            px = reduce(s, ps, q, px, 'market', W.mark[s]); return {'avgPrice': str(px)}
        def stop(self, s, ps, qty, price):
            W.n += 1; tag = f'o:{W.n}'; W.stops[tag] = dict(s=s, ps=ps, q=float(qty), p=float(price))
            log_fill(s, ps, 'stop_set', 'order', float(qty), float(price)); return tag
        def cancel(self, s, tag): W.stops.pop(tag, None)
        def open_stop_tags(self, s): return {k for k, v in W.stops.items() if v['s'] == s}
        def leverage_max(self, s): return 50
        def cancel_all(self, s):
            for k in [k for k, v in W.stops.items() if v['s'] == s]: W.stops.pop(k)

    class Measured(Fake):
        # B1: an order answer the engine can correlate (orderId / clientOrderId / executedQty / FILLED) and its trade rows
        def _answer(self, s, o, cid):
            q, px, fee = W.last; W.n += 1; oid = W.n
            W.trades[(s, oid)] = [dict(id=oid, orderId=oid, symbol=s, qty=str(q), price=str(px), commission=str(fee),
                                       commissionAsset=E._quote_asset(s), maker=False)]
            return dict(o, orderId=oid, clientOrderId=cid or f'zbr{oid}', executedQty=str(q), status='FILLED')
        def open(self, s, ps, qty, cid=None): return self._answer(s, super().open(s, ps, qty), cid)
        def close(self, s, ps, qty, cid=None): return self._answer(s, super().close(s, ps, qty), cid)
        def user_trades(self, s, oid): return [dict(r) for r in W.trades.get((s, oid), ())]

    saved = (E.Futures, E.now_utc, E.time)
    lg = logging.getLogger('zackbot'); lvl = lg.level
    if quiet: lg.setLevel(logging.WARNING)
    fake_now = [0.0]
    stop_set = ({stop_at} if isinstance(stop_at, tuple) else set(stop_at)) if stop_at else set()
    snaps = {}
    try:
        E.Futures = Measured if measured_fills else Fake
        E.now_utc = lambda: pd.Timestamp(fake_now[0], unit='s', tz='UTC').to_pydatetime()
        E.time = types.SimpleNamespace(time=lambda: fake_now[0], sleep=lambda x: None)
        tmp = tempfile.mkdtemp(prefix='zb_replay_')
        eng = E.Engine(dict(MODE='paper', API_KEY='x', API_SECRET='y'), tmp)
        eng.S['SLEEVES'] = sleeves; eng.S['UNIVERSE'] = syms; eng.S['SYMBOLS_ON'] = {s: True for s in syms}
        eng.S['CAPITAL_CAP'] = 0; eng.S['MAX_LEVERAGE'] = 10
        if settings: eng.S.update(copy.deepcopy(settings))
        eng.equity_hist = []; eng.record_equity = lambda e: None
        if measured_fills: eng._enr = SyncEnricher()
        eng.connect()

        def mng():
            if measured_fills: eng.apply_costs()        # B1: measured fills reach their lots before manage acts
            eng.manage(dict(W.mark))
        tf = next((k for k, v in E.TF_SEC.items() if v == tf_sec), '4h')

        def walk(pts_by_sym):
            # time-merged (Cowork #187 F1): every symbol's points of one time stamp are walked before any later time, so
            # manage() never sees one symbol's market minutes ahead of another's; points keep their order per symbol
            by_t = {}
            for s, pts in pts_by_sym.items():
                for p in pts: by_t.setdefault(p[2], {}).setdefault(s, []).append(p)
            for t in sorted(by_t):
                W.t_ms = t
                for s, pts in by_t[t].items():
                    for p in pts:
                        move({s: p[0]}, at_open=bool(p[1]))
                        if eng.state['lots']: mng()
        curve = []
        for i in range(t0, N - 1):
            W.i = i
            fake_now[0] = D[syms[0]].t[i].timestamp() + tf_sec + 15
            W.t_ms = int(D[syms[0]].t[i + 1].value // 10 ** 6)
            move({s: D[s].o[i + 1] for s in syms}, at_open=True)   # C13f: the open runs the simulated stops (was mark.update)
            def fund_at_open():                         # funding at T = this candle's open over the lots held at T
                t_j = int(D[syms[0]].t[i + 1].value // 10 ** 6)
                W.cash -= fund_fn(t_j, t_j + tf_sec * 1000, [dict(l) for l in W.lots])
            if fund_fn is not None and pre_fn is not None: fund_at_open()     # entries come later than T
            if pre_fn is not None:                      # SCALP-EXEC-15M-01 latency: the market moves before the decision
                held0 = {}
                for l_ in eng.state['lots'].values(): held0.setdefault(l_['symbol'], set()).add(1 if l_['side'] == 'LONG' else -1)
                walk(pre_fn(i + 1, held0, eng.state['lots']) or {})
            eng.cycle(tf)
            j = i + 1
            if fund_fn is not None and pre_fn is None: fund_at_open()         # an entry at T counts (entry == T)
            # FBL-BT01: the backtester's path policy (backtest.path_points). A doji takes the worst-case path of the side
            # held on that coin (long: high first); with no lot, or both sides held, it keeps the green order
            held = {}
            for l_ in eng.state['lots'].values(): held.setdefault(l_['symbol'], set()).add(1 if l_['side'] == 'LONG' else -1)
            pth = {s: B.path_points(D[s].o[j], D[s].h[j], D[s].l[j], D[s].c[j], next(iter(held[s])))
                   if len(held.get(s, ())) == 1 else B.path_points(D[s].o[j], D[s].h[j], D[s].l[j], D[s].c[j], 1)
                   if D[s].c[j] != D[s].o[j] else (D[s].o[j], D[s].l[j], D[s].h[j], D[s].c[j]) for s in syms}
            legs = [{s: pth[s][1] for s in syms}, {s: pth[s][2] for s in syms}, {s: pth[s][3] for s in syms}]
            fine = path_fn(j, held, eng.state['lots']) if path_fn is not None else None
            if fine:                                    # SCALP-EXEC-15M-01: caller-supplied points (e.g. the candle's 1m bars)
                legs = [{s: tgt[s] for s in syms if s not in fine} for tgt in legs]
                walk(fine)
                W.t_ms = int(D[syms[0]].t[j].value // 10 ** 6) + tf_sec * 1000 - 1
            for li, tgt in enumerate(legs):
                if not tgt: continue
                a = dict(W.mark)
                for k in range(1, steps + 1):
                    move({s: a[s] + (tgt[s] - a[s]) * k / steps for s in tgt})
                    if eng.state['lots']: mng()
                    if stop_at and (j, li, k) in stop_set:
                        import copy as _c, csv as _csv
                        f = os.path.join(tmp, 'trades.csv')
                        ev = list(_csv.DictReader(open(f))) if os.path.exists(f) else []
                        snaps[(j, li, k)] = dict(lots=_c.deepcopy(eng.state['lots']), events=ev, history=_c.deepcopy(eng.history),
                                                 stops=_c.deepcopy(W.stops), cash=W.cash)
                        if len(snaps) == len(stop_set):
                            return snaps[stop_at] if isinstance(stop_at, tuple) else snaps
                if not eng.state['lots']: mng()
            if fund_fn is None and not costs_off: W.cash -= sum(l['q'] * W.mark[l['s']] * B.FUND_PER_BAR * tf_sec / 14400 for l in W.lots)
            curve.append((D[syms[0]].t[j], equity()))
        if measured_fills: eng.apply_costs()            # the last exits' measured fills reach their history rows
    finally:
        E.Futures, E.now_utc, E.time = saved
        lg.setLevel(lvl)
        if 'tmp' in locals():
            try: E.close_fill_writer(os.path.join(tmp, 'fills.jsonl'))   # T05: no telemetry thread left behind
            except Exception: pass
            try: E.close_fill_writer(os.path.join(tmp, 'trade_audit.jsonl'))   # T05a: nor an audit writer thread
            except Exception: pass
    cv = pd.Series([c for _, c in curve], index=[t for t, _ in curve])
    if not compare:
        return dict(engine_curve=cv, history=eng.history, missed=eng.missed, stops=dict(W.stops), lots=copy.deepcopy(eng.state['lots']),
                    exchange_lots=[dict(l) for l in W.lots])
    bk = B.Book({s: raw[s] for s in syms})
    def order(sl):   # same coin order as the live engine (sleeve_symbols) and the app's backtests: first signal in this order wins a free slot
        base = E.CORE8 if sl['symbols'] == 'core8' else syms if sl['symbols'] == 'all' else sl['symbols']
        return [x for x in base if x in syms]
    cfg = [dict(key=s['key'], share=s['share'], risk=s['risk'], max_pos=s['max_pos'], symbols=order(s), sides=s['sides'], mgmt=s['mgmt'],
                **{k: s[k] for k in ('when', 'trail_entry', 'pump_guard') if s.get(k) is not None}) for s in sleeves]
    t2, c2 = B.run(bk, cfg, start=start, max_lev=10, t0=D[syms[0]].t[t0 + 1], fund_per_bar=B.FUND_PER_BAR * tf_sec / 14400,
                    exchange_rules=exchange_rules)

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
