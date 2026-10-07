"""Portfolio backtester for ZackBot strategies (same rules the live engine uses).  ENGINE_VERSION v3

Fills: entries at next candle open; a stop inside the candle beats any target in it (conservative), safety orders
met before the stop on the path fill first;
targets/adds fill at their trigger price; signal exits at the candle close.
Intrabar order (FBL-BT01): every other intrabar event is fired along ONE declared price path, see path_points().
Costs: taker fee + slippage per side, funding cost on every open position.
"""
import numpy as np
import pandas as pd
import strategies as S
import feasibility as F

FEE, SLIP, FUND_PER_BAR = 0.0005, 0.0002, 0.00005
FEE_MAKER = 0.0002    # maker fee (post-only limit entries)
BE_BUF = 0.0015
VERSION = 'v3.1'        # breakeven stop sits just past entry so fees are covered     # 0.01%/8h on 4h candles
MIN_NOTIONAL = {'BTCUSDT': 50, 'ETHUSDT': 20, 'LINKUSDT': 20}    # legacy floor (pre-BT02): notional only, no step / minQty


def legacy_rule(sym):
    """The pre-BT02 backtest floor as a rule: min notional only (MIN_NOTIONAL, default 5), no step rounding, no minQty.
    Used for every symbol when run(exchange_rules=None), and for symbols missing from a snapshot (reported as unknown)."""
    return dict(step=None, min_qty=0.0, min_notional=float(MIN_NOTIONAL.get(sym, 5)))


class Book:
    """Shared data: indicator frames + cross-sectional context for a set of symbols."""
    def __init__(self, raw):      # raw: {sym: DataFrame t,o,h,l,c,v}
        # align every coin on the SAME candle timestamps (intersection), so a missing candle can never shift one coin
        # against another; bars missing inside the common window are reported in self.gaps
        clean = {s: d.drop_duplicates('t').sort_values('t').reset_index(drop=True) for s, d in raw.items()}
        common = None
        for d in clean.values():
            ts = pd.Index(pd.to_datetime(d.t))
            common = ts if common is None else common.intersection(ts)
        common = common.sort_values()
        self.gaps = {}
        if len(common):
            lo, hi = common[0], common[-1]
            for s_, d in clean.items():
                inside = pd.to_datetime(d.t); inside = inside[(inside >= lo) & (inside <= hi)]
                if len(inside) != len(common): self.gaps[s_] = int(len(inside) - len(common))
        self.syms = list(clean)
        self.d = {s_: S.indicators(d[pd.to_datetime(d.t).isin(common)].reset_index(drop=True)) for s_, d in clean.items()}
        self.t = self.d[self.syms[0]].t
        btc = 'BTCUSDT' if 'BTCUSDT' in self.d else self.syms[0]
        self.ctx = S.build_context(self.d, btc)
        self.arr = {s: {k: self.d[s][k].values for k in ('o', 'h', 'l', 'c', 'atr', 'e50', 'st')} for s in self.syms}
        self._sig = {}

    def raw_sig(self, key, sym, params=None):
        """Signals without the strategy's default side mask (the sleeve decides long/short/both)."""
        k = (key, sym, str(params))
        if k not in self._sig:
            self._sig[k] = S.signals(key, self.d[sym], self.ctx[sym], params, mask_sides=False)
        return self._sig[k]


def kelly_mult(sl):
    """Fractional-Kelly risk multiplier from this sleeve's recent trades (R multiples). 1.0 until enough history."""
    k = sl['cfg'].get('kelly')
    if not k or len(sl['hist']) < k.get('min_trades', 20): return 1.0
    h = np.array(sl['hist'][-k.get('n', 40):])
    w, l = h[h > 0], h[h <= 0]
    if not len(w) or not len(l): return k.get('max', 2.0) if len(w) else k.get('min', 0.25)
    p, b = len(w) / len(h), w.mean() / abs(l.mean())
    f = (p * b - (1 - p)) / b                     # Kelly fraction of capital per unit risk
    base = k.get('ref', 0.1)                      # Kelly value that maps to 1x base risk
    return float(np.clip(k.get('frac', 0.5) * f / base * 2, k.get('min', 0.25), k.get('max', 2.0)))


def _rules_on(rules, name):
    r = (rules or {}).get(name) or {}
    return r if r.get('mode') == 'enforce' else None


def _btc_move_1h(book, btc, bar_sec, btc1h=None):
    """|% close-to-close move of BTC over the last closed hour| at each bar's close.
    1h (or finer) books use their own candles; a 4h book uses btc1h (DataFrame t,c) when given, else a proxy:
    the 4h close-to-close move divided by sqrt(4) (random-walk scaling)."""
    c = pd.Series(book.d[btc].c.values)
    if bar_sec <= 3600:
        k = max(1, int(round(3600 / bar_sec)))
        return (c / c.shift(k) - 1).abs().fillna(0).values * 100
    if btc1h is not None and len(btc1h):
        h = btc1h.sort_values('t'); h = pd.DataFrame(dict(ct=pd.to_datetime(h.t) + pd.Timedelta(hours=1),
                                                          mv=(h.c / h.c.shift() - 1).abs().fillna(0).values * 100))
        left = pd.DataFrame(dict(ct=pd.to_datetime(book.t) + pd.Timedelta(seconds=bar_sec)))
        h['ct'] = h['ct'].astype('datetime64[ns]'); left['ct'] = left['ct'].astype('datetime64[ns]')
        out = pd.merge_asof(left, h, on='ct', direction='backward').mv.fillna(0).values
        return out
    return (c / c.shift() - 1).abs().fillna(0).values * 100 / np.sqrt(bar_sec / 3600)


def _gov_rules(governor):
    if not governor or governor.get('mode', 'auto') == 'off': return []
    out = []
    for r in governor.get('rules', []):
        th = r.get('then') or {}
        if 'risk_mult' not in th: continue                          # profile switches are live-only
        out.append(dict(r, mult=float(np.clip(float(th['risk_mult']), 0.05, 2.0)), on=False, peak=None))
    return out


def _gov_step(G, mtm, peak, growth, dd):
    """Advance the governor rules one step; returns the combined risk multiplier (product, clipped 0.05-2)."""
    mult = 1.0
    for r in G:
        cond = (growth >= r['value']) if r['if'] == 'growth_gte' else (dd >= r['value'])
        if not r['on']:
            if cond: r['on'], r['peak'] = True, peak
        elif r.get('until') == 'new_high':
            if mtm > r['peak']: r['on'] = False
        elif r.get('until') == 'reset':
            pass
        else:
            r['on'] = cond
        if r['on']: mult *= r['mult']
    return float(np.clip(mult, 0.05, 2.0))


def path_points(o, h, l, c, side, worst=False):
    """THE intrabar price-path policy of the backtester (FBL-BT01) - one place, used for every intrabar event:
      green candle (c > o): open -> low -> high -> close
      red candle   (c < o): open -> high -> low -> close
      doji (c == o), and every candle when worst=True: the worst case for THIS position's side - favourable extreme first,
        adverse extreme last: long open -> high -> low -> close, short open -> low -> high -> close. (A target or add level
        recomputed at the adverse extreme can then never be reached in the same candle, and a stop raised at the
        favourable extreme is checked against the adverse one.)
    Not part of the walk: a gap through the stop at the open (filled at the open, no adds). Stop-first convention kept
    for the ambiguous case only: if the open's stop is inside the candle, no target exit is taken in that candle (adds
    still fire in path order before the stop - Codex round 1). Entries fill at the next open (or on the candle path for
    trailing entries, which keep their own c >= o rule); signal / time exits and the liquidation check use the close / the adverse extreme."""
    if worst or c == o:
        return (o, h, l, c) if side == 1 else (o, l, h, c)
    return (o, l, h, c) if c > o else (o, h, l, c)


def run(book, sleeves, start=500.0, max_lev=10.0, daily_halt=0.08, t0=None, t1=None, warmup=220, fund_per_bar=None, pessimistic='path',
        entry_order='market', fee_maker=None, maker_fallback=True, pump_guard=None, risk_rules=None, governor=None,
        maint_margin=0.005, btc1h=None, exchange_rules=None):
    """pessimistic: how a stop tightened during a candle (breakeven / trailing / runner) is checked against that same candle.
    'path' (default): the declared price path (path_points: candle colour, doji = worst case for the side); 'worst': the
    worst-case path (favourable extreme first, adverse last) on every candle; False: never (old v2).
    Since FBL-BT01 the same path orders every intrabar event (DCA fills, basket TP, pyramid adds, tp1 / ladder / tp_r).
    v3.1 options (all off by default except the liquidation check, which only fires if margin is actually exhausted):
      entry_order 'maker': limit at the signal close, filled (maker fee) if the next candle trades through it, else
                  at the next open with taker fee (maker_fallback) or skipped
      pump_guard  {'max_candle_atr', 'btc_1h_pct'} global (a sleeve's own 'pump_guard' wins)
      risk_rules  engine RISK_RULES dict; rules in mode 'enforce' are applied (coin_cap, open_risk_cap, correlated_cap,
                  btc_breaker incl. tighten). funding_filter has no historical data here and is ignored
      governor    {'rules': [{'if': 'growth_gte'|'dd_gte', 'value': pct, 'then': {'risk_mult': m}, 'until': 'new_high'|'reset'|None}]}
      maint_margin cross-margin liquidation check per bar (0 = off)
      btc1h       optional BTC 1h candles (t,c) for the 'BTC moved X% in the last hour' rules on a 4h book
      exchange_rules BT02 execution feasibility: a snapshot (feasibility / exchange_rules.py) or {symbol: rule}. Entries and
                  adds (DCA safety orders, pyramid adds) are floored to the symbol's step and skipped - never rounded up - when
                  below minQty / minNotional (feasibility.size_check, the engine's own check). None (default) = the legacy
                  floor (notional only, MIN_NOTIONAL), unchanged results; symbols missing from a snapshot also use the legacy
                  floor and are listed as 'unknown'. Skips are counted in cv.attrs['feasibility'].
    sleeve extras: 'when' ('any'|'bull'|'bear'|'range'), 'trail_entry' {'dev_atr','max_bars'}, 'pump_guard', mgmt 'tps', 'ttp'."""
    FPB = FUND_PER_BAR if fund_per_bar is None else fund_per_bar
    FM = FEE_MAKER if fee_maker is None else fee_maker
    """sleeves: list of dict(key, share, risk, max_pos, sides(optional: long/short/both), mgmt(optional overrides),
    symbols(optional subset), params(optional))."""
    syms_all = book.syms
    T = book.t.values
    bar_sec = float(pd.Series(T).diff().median() / np.timedelta64(1, 's')) if len(T) > 1 else 14400.0
    idx = np.arange(warmup, len(T))
    if t0 is not None: idx = idx[T[idx] >= np.datetime64(t0)]
    if t1 is not None: idx = idx[T[idx] < np.datetime64(t1)]
    btc = 'BTCUSDT' if 'BTCUSDT' in book.d else syms_all[0]
    SL = []
    for sl in sleeves:
        st = S.STRATEGIES[sl['key']]
        m = S.merge_mgmt(sl['key'], sl.get('mgmt'))
        sides = sl.get('sides', st['sides'])
        syms = [s for s in sl.get('symbols', syms_all) if s in syms_all]
        sigs = {s: book.raw_sig(sl['key'], s, sl.get('params')) for s in syms}
        for s in syms:
            g = dict(sigs[s])
            if sides == 'long': g['se'] = np.zeros(len(T), bool)
            if sides == 'short': g['le'] = np.zeros(len(T), bool)
            sigs[s] = g
        volr = {}
        if sl.get('vol_max_pct'):
            for s in syms:
                ap = (book.d[s].atr / book.d[s].c)
                volr[s] = ap.rolling(180, min_periods=60).rank(pct=True).fillna(0.5).values
        SL.append(dict(cfg=sl, m=m, syms=syms, sigs=sigs, pos={}, pend={}, tpend={}, volr=volr, hist=[],
                       tps=S.norm_tps(m.get('tps')), pg=sl.get('pump_guard') or pump_guard or None))

    # ---- shared context for the v3.1 rules (computed only when used)
    RR = {k: _rules_on(risk_rules, k) for k in ('coin_cap', 'open_risk_cap', 'correlated_cap', 'btc_breaker')}
    need_mv = RR['btc_breaker'] or any(x['pg'] and x['pg'].get('btc_1h_pct') for x in SL)
    MV = _btc_move_1h(book, btc, bar_sec, btc1h) if need_mv else None
    RG = S.regime(book.d[btc]) if any(x['cfg'].get('when', 'any') not in (None, 'any') for x in SL) else None
    RG = {k: RG[k].values for k in ('bull', 'bear', 'range')} if RG is not None else None
    corr_w = max(20, int(round(30 * 86400 / bar_sec)))
    G = _gov_rules(governor)
    gmult, peak_mtm = 1.0, start
    breaker_until = -1                                   # bar index until which entries are paused
    liqs, blocked = 0, {}
    # ---- BT02: exchange filters (one rule per symbol) and skipped-signal accounting
    XR = F.snapshot_rules(exchange_rules)
    RULES = {s: (XR[s] if XR is not None and s in XR else legacy_rule(s)) for s in syms_all}
    ADD_RULES = {s: (XR or {}).get(s) for s in syms_all}  # adds are checked only against a real rule (legacy: never, as before)
    FEAS = dict(mode='legacy' if XR is None else 'rules', attempts=0, executed=0, rule_blocked=0, skipped={}, by_symbol={}, by_slot={},
                add_skipped={}, skips=[], unknown_symbols=sorted(s for s in syms_all if XR is not None and s not in XR))
    add_seen = set()

    def feas_skip(sl, s, side, i, d, px, qty_raw, add=None):
        sid = sl['cfg'].get('id') or sl['cfg']['key']
        if add:
            k = (id(sl), s, add)
            if k in add_seen: return                       # one count per position and order level, not per candle
            add_seen.add(k); FEAS['add_skipped'][d['code']] = FEAS['add_skipped'].get(d['code'], 0) + 1; return
        FEAS['skipped'][d['code']] = FEAS['skipped'].get(d['code'], 0) + 1
        for grp, key in ((FEAS['by_symbol'], s), (FEAS['by_slot'], sid)):
            g = grp.setdefault(key, dict(attempts=0, skipped=0)); g['skipped'] += 1
        if len(FEAS['skips']) < 500:
            r = RULES[s]
            FEAS['skips'].append(dict(t=str(pd.Timestamp(T[i])), slot=sid, sym=s, side=side, code=d['code'], reason=d['reason'],
                                      qty_raw=float(qty_raw), qty=float(d['qty']), px=float(px), notional=round(float(d['qty'] * px), 4),
                                      min_notional=r['min_notional'], min_qty=r['min_qty'], step=r['step']))

    eq, trades, curve = start, [], []
    day, day_start, halted = None, eq, False
    try: days = pd.to_datetime(T).tz_localize('UTC').tz_convert('Africa/Cairo').date      # trading day = Cairo day, like live
    except Exception: days = (pd.to_datetime(T) + pd.Timedelta(hours=3)).date

    def close(sl, s, p, px, frac, i, why):
        nonlocal eq
        q = p['qty'] * frac
        px = px * (1 - p['side'] * SLIP)
        pnl = p['side'] * (px - p['avg']) * q - q * px * FEE
        eq += pnl
        p['realized'] += pnl
        p['qty'] -= q
        if p['qty'] <= 1e-12:
            sl['hist'].append(p['realized'] / p['risk'])
            trades.append(dict(sleeve=sl['cfg']['key'], sym=s, side=p['side'], i_in=p['i'], i_out=i,
                               pnl=p['realized'], R=p['realized'] / p['risk'], why=why))
            return True
        return False

    def notional(sl, i):
        return sum(p['qty'] * book.arr[s]['c'][i - 1] for s, p in sl['pos'].items())

    def lot_risk(s, p, i):
        """Open risk to the stop (incl. unfilled DCA safety orders)."""
        r = max(0.0, p['side'] * (p['avg'] - p['stop'])) * p['qty']
        if 'levels' in p:
            for k in range(p['dca'], len(p['levels'])):
                r += p['q0'] * p['w'][k] * abs(p['levels'][k] - p['stop'])
        return r

    def corr(a, b, i):
        ca, cb = book.arr[a]['c'][max(0, i - corr_w - 1):i], book.arr[b]['c'][max(0, i - corr_w - 1):i]
        if len(ca) < 20: return 0.0
        ra, rb = np.diff(ca) / ca[:-1], np.diff(cb) / cb[:-1]
        with np.errstate(all='ignore'):
            v = np.corrcoef(ra, rb)[0, 1]
        return 0.0 if not np.isfinite(v) else float(v)

    def rule_block(s, side, qty, px, new_risk, i, add=False):
        if not (RR['coin_cap'] or RR['open_risk_cap'] or RR['correlated_cap']): return None
        allp = [(s_, p) for x in SL for s_, p in x['pos'].items()]
        cap_now = eq + sum(p['side'] * (book.arr[s_]['c'][i - 1] - p['avg']) * p['qty'] for s_, p in allp)
        if cap_now <= 0: return 'no capital'
        if RR['coin_cap']:
            tot = sum(p['qty'] * book.arr[s_]['c'][i - 1] for s_, p in allp if s_ == s) + qty * px
            if tot > RR['coin_cap'].get('x', 3.0) * cap_now: return 'coin_cap'
        if RR['open_risk_cap']:
            tot = sum(lot_risk(s_, p, i) for s_, p in allp) + new_risk
            if tot > RR['open_risk_cap'].get('pct', 15.0) / 100 * cap_now: return 'open_risk_cap'
        if RR['correlated_cap'] and not add:
            n, rho = RR['correlated_cap'].get('n', 4), RR['correlated_cap'].get('rho', 0.8)
            same = {s_ for s_, p in allp if p['side'] == side and s_ != s}
            if sum(1 for s_ in same if corr(s, s_, i) > rho) >= n: return 'correlated_cap'
        return None

    def open_pos(sl, s, side, px, atr, i, sl_eq, fee):
        """Size and open a position (same rules as the live engine). Returns the position or None."""
        nonlocal eq
        m, cfg = sl['m'], sl['cfg']
        risk_usd = sl_eq * cfg['risk'] * kelly_mult(sl) * gmult
        z = F.risk_qty(m, risk_usd, px, atr, side)          # shared with engine.open_lot
        qty, stop, stop_dist, lv, w = z['qty'], z['stop'], z['R'], z['levels'], z['weights']
        qty_raw = qty
        cap = max(0.0, max_lev * sl_eq - notional(sl, i)) / px
        qty = min(qty, cap)
        sid = cfg.get('id') or cfg['key']
        FEAS['attempts'] += 1
        for grp, key in ((FEAS['by_symbol'], s), (FEAS['by_slot'], sid)):
            grp.setdefault(key, dict(attempts=0, skipped=0))['attempts'] += 1
        d = F.size_check(qty_raw, qty, px, RULES[s])         # BT02: the engine's exchange-filter check (floor, never round up)
        if not d['ok']: feas_skip(sl, s, side, i, d, px, qty_raw); return None
        qty = d['qty']
        if RR['coin_cap'] or RR['open_risk_cap'] or RR['correlated_cap']:
            why = rule_block(s, side, qty, px, risk_usd * qty / qty_raw, i)
            if why:
                blocked[why] = blocked.get(why, 0) + 1; FEAS['rule_blocked'] += 1      # sized fine, refused by a risk rule
                for grp, key in ((FEAS['by_symbol'], s), (FEAS['by_slot'], sid)): grp[key]['rule_blocked'] = grp[key].get('rule_blocked', 0) + 1
                return None
        FEAS['executed'] += 1
        eq -= qty * px * fee
        p = dict(side=side, qty=qty, q0=qty, avg=px, e0=px, stop=stop, R=stop_dist, risk=risk_usd, i=i,
                 best=px, realized=-qty * px * fee, tp1=False, adds=0, dca=0, atr0=atr, qmax=qty, tps_done=set())
        if 'pyramid' in m: p['next_add'] = px + side * m['pyramid']['step_r'] * stop_dist
        if 'dca' in m:
            p['levels'] = lv[1:]; p['w'] = w[1:]
            p['tp'] = px + side * m['dca']['tp_atr'] * atr
        sl['pos'][s] = p
        return p

    def entry_filters(sl, s, side, i):
        """Signal-time filters shared with the engine's entry gate: regime, pump guard, BTC breaker."""
        cfg = sl['cfg']
        when = cfg.get('when', 'any')
        if RG is not None and when not in (None, 'any') and not RG[when][i]: return 'regime'
        pg = sl['pg']
        if pg:
            a = book.arr[s]
            if pg.get('max_candle_atr') and a['atr'][i] > 0 and (a['h'][i] - a['l'][i]) / a['atr'][i] > pg['max_candle_atr']: return 'pump_candle'
            if pg.get('btc_1h_pct') and MV is not None and MV[i] > pg['btc_1h_pct']: return 'pump_btc'
        if RR['btc_breaker'] and i <= breaker_until: return 'btc_breaker'
        return None

    def ratchet(m, p, atr):
        """Stop tightening from the best price reached so far (breakeven trigger, chandelier trail, trailing TP, runner).
        Called at the end of every path leg; a stop raised here can only be hit by a LATER leg of the path."""
        sd = p['side']; RUN = m.get('runner'); TTP = m.get('ttp')
        tighten = lambda x: max(p['stop'], x) if sd == 1 else min(p['stop'], x)
        if m.get('be_r') and sd * (p['best'] - (p['e0'] + sd * m['be_r'] * p['R'])) >= 0:
            p['stop'] = tighten(p['avg'])
        if m.get('trail_atr'):                               # chandelier from the best price
            p['stop'] = tighten(p['best'] - sd * m['trail_atr'] * atr)
        if TTP and p['R'] > 0 and sd * (p['best'] - p['e0']) / p['R'] >= TTP.get('at_r', 2.0):
            p['stop'] = tighten(p['best'] * (1 - sd * TTP.get('dev_pct', 3.0) / 100))   # trailing take-profit
        if RUN and p['R'] > 0:                               # runner: breakeven first, then lock profit in steps
            bestR = sd * (p['best'] - p['e0']) / p['R']
            if bestR >= RUN.get('be_r', 2.0):
                p['stop'] = tighten(p['avg'] * (1 + sd * BE_BUF))
            lock = np.floor(bestR / RUN.get('step_r', 99)) * RUN.get('step_r', 99) - RUN.get('gap_r', 99)
            if RUN.get('giveback') and bestR >= RUN.get('gb_from', 4.0):
                lock = max(lock, bestR * (1 - RUN['giveback']))
            if lock > 0:
                p['stop'] = tighten(p['e0'] + sd * lock * p['R'])

    def walk_path(sl, s, p, i, o, h, l, c, atr):
        """FBL-BT01: walk candle i along the declared price path (path_points) and fire every intrabar event in path order.
        A level set or recomputed at some point of the path (basket TP after a safety order, next pyramid level, a raised
        stop) can only be reached by the price moves AFTER that point. Inside one monotonic leg, levels fire in price order.
        The stop (as it stood at the open, or raised earlier in the candle) is an adverse-leg event like the safety orders:
        every safety order met before it on the way down (long) fills first, then the stop closes the enlarged basket.
        Stop-first convention (kept, for the ambiguous case only): when the open's stop is inside this candle's range, the
        candle ends at that stop - no target / tp1 / ladder / tp_r / basket TP is taken in it, even where the path would
        reach the target first. Adds (safety orders, pyramid adds) still fire in path order before the stop.
        Returns True when the position was fully closed (the caller removes it)."""
        nonlocal eq
        m, cfg, sd = sl['m'], sl['cfg'], p['side']
        RUN = m.get('runner'); TTP = m.get('ttp'); TPS = sl['tps']; py = m.get('pyramid')
        fav_reached = lambda lvl, x: sd * (x - lvl) >= 0     # price x at / through a favourable level
        adv_reached = lambda lvl, x: sd * (lvl - x) >= 0     # price x at / through an adverse level (safety order, stop)
        blocked_ = dict(dca=False, add=False)                # an add refused by a gate is not retried in this candle
        stop_open = p['stop']
        stop_in_candle = adv_reached(stop_open, l if sd == 1 else h)   # the candle WILL end at a stop: no exits at targets

        def adverse_leg(a_, b_, first):
            """Price moves against the position from a_ to b_: DCA safety orders and the stop, in the order price meets
            them. Exact tie safety level == stop: the safety order fills first, then the stop closes the larger basket (the
            worse outcome for the account). A gap through the stop at the open is handled before the walk (filled at the
            open, no adds)."""
            nonlocal eq
            st = p['stop'] if pessimistic else stop_open      # pessimistic=False (old v2): a stop raised in-candle is not tested
            while True:
                cand = []
                if 'dca' in m and not blocked_['dca'] and p['dca'] < len(p['levels']) and adv_reached(p['levels'][p['dca']], b_):
                    cand.append((sd * p['levels'][p['dca']], 1, 'dca'))
                if not first and adv_reached(st, b_):
                    cand.append((sd * st, 0, 'stop'))
                if not cand: return False
                ev = max(cand)[2]                            # first level met on the way (tie: the safety order, then the stop)
                if ev == 'stop':
                    px = st if sd * (a_ - st) >= 0 else a_                     # already through it -> filled where price is
                    close(sl, s, p, px, 1, i, 'stop'); return True
                lvl = p['levels'][p['dca']]; q = p['q0'] * p['w'][p['dca']]
                lvl = min(lvl, a_) if sd == 1 else max(lvl, a_)              # gapped through the level -> filled where price is
                if RR['btc_breaker'] and i <= breaker_until:                 # same breaker policy as live
                    pol = RR['btc_breaker'].get('dca', 'pause')
                    if pol == 'pause': blocked_['dca'] = True; continue
                    if pol == 'half_size': q *= 0.5
                if halted or notional(sl, i) + q * lvl > max_lev * eq * cfg['share']: blocked_['dca'] = True; continue
                if rule_block(s, sd, q, lvl, 0.0, i, add=True): blocked_['dca'] = True; continue   # same add gate as live
                d = F.size_check(q, q, lvl, ADD_RULES[s])     # BT02: the engine's _add_qty check (floor to step, minimums)
                if d['ok'] is False: feas_skip(sl, s, sd, i, d, lvl, q, add=('dca', p['i'], p['dca'])); blocked_['dca'] = True; continue
                q = d['qty']
                p['avg'] = (p['avg'] * p['qty'] + lvl * q) / (p['qty'] + q); p['qty'] += q
                eq -= q * lvl * FEE; p['realized'] -= q * lvl * FEE; p['dca'] += 1
                p['qmax'] = max(p['qmax'], p['qty'])
                p['tp'] = p['avg'] + sd * m['dca']['tp_atr'] * p['atr0']     # new basket TP: reachable only by later moves

        def favourable_leg(a_, b_):
            """Price moves in favour from a_ to b_: basket TP, pyramid adds, tp1, ladder, tp_r - in price order."""
            nonlocal eq
            while True:
                cand = []
                if 'dca' in m and np.isfinite(p['tp']) and fav_reached(p['tp'], b_):
                    cand.append((sd * p['tp'], 0, 'tp', p['tp']))
                if py and not blocked_['add'] and p['adds'] < py['n'] and fav_reached(p['next_add'], b_):
                    cand.append((sd * p['next_add'], 1, 'add', p['next_add']))
                if m.get('tp1_r') and not p['tp1']:
                    lv = p['e0'] + sd * m['tp1_r'] * p['R']
                    if fav_reached(lv, b_): cand.append((sd * lv, 2, 'tp1', lv))
                if TPS:                                       # take-profit ladder: the first level not yet done
                    k = next((k for k in range(len(TPS)) if k not in p['tps_done']), None)
                    if k is not None:
                        lv = p['e0'] + sd * TPS[k][0] * p['R']
                        if fav_reached(lv, b_): cand.append((sd * lv, 3, 'lad', lv, k))
                if m.get('tp_r') and not RUN and not TTP:
                    lv = p['e0'] + sd * m['tp_r'] * p['R']
                    if fav_reached(lv, b_): cand.append((sd * lv, 4, 'tpr', lv))
                if stop_in_candle: cand = [x for x in cand if x[2] == 'add']   # stop-first convention: adds only, no exits
                if not cand: return False
                e = min(cand); ev, lv = e[2], e[3]           # first level met on the way
                if ev == 'tp':
                    if RUN:      # runner: bank part at the basket target, keep the rest at breakeven
                        if close(sl, s, p, lv, RUN.get('dca_frac', 1.0), i, 'tp' if RUN.get('dca_frac', 1.0) >= 1 else 'tp1'):
                            return True
                        p['tp'] = np.inf * sd; p['tp1'] = True; p['dca'] = len(p['levels'])
                        p['stop'] = max(p['stop'], p['avg'] * (1 + BE_BUF)) if sd == 1 else min(p['stop'], p['avg'] * (1 - BE_BUF))
                        p['e0'], p['R'] = p['avg'], max(p['R'], abs(p['avg'] - p['stop']) or p['R'])
                    else:
                        close(sl, s, p, lv, 1, i, 'tp'); return True
                elif ev == 'add':
                    q = p['q0'] * py['frac']
                    lvl = max(lv, a_) if sd == 1 else min(lv, a_)                # gapped through the add level -> filled where price is
                    if halted or (RR['btc_breaker'] and i <= breaker_until) or notional(sl, i) + q * lvl > max_lev * eq * cfg['share']:
                        blocked_['add'] = True; continue
                    if rule_block(s, sd, q, lvl, q * max(0.0, sd * (lvl - p['stop'])), i, add=True): blocked_['add'] = True; continue
                    d = F.size_check(q, q, lvl, ADD_RULES[s])  # BT02: the engine's _add_qty check
                    if d['ok'] is False: feas_skip(sl, s, sd, i, d, lvl, q, add=('add', p['i'], p['adds'])); blocked_['add'] = True; continue
                    q = d['qty']
                    p['avg'] = (p['avg'] * p['qty'] + lvl * q) / (p['qty'] + q); p['qty'] += q
                    eq -= q * lvl * FEE; p['realized'] -= q * lvl * FEE; p['adds'] += 1
                    p['qmax'] = max(p['qmax'], p['qty'])
                    p['next_add'] += sd * py['step_r'] * p['R']
                elif ev == 'tp1':
                    p['tp1'] = True
                    if close(sl, s, p, lv, m.get('tp1_frac', 0.5), i, 'tp1'): return True
                elif ev == 'lad':
                    p['tps_done'].add(e[4])
                    if close(sl, s, p, lv, min(1.0, p['qmax'] * TPS[e[4]][1] / p['qty']), i, 'tp_ladder'): return True
                else:
                    close(sl, s, p, lv, 1, i, 'tp'); return True

        pts = path_points(o, h, l, c, sd, worst=(pessimistic == 'worst'))
        for k in range(len(pts)):
            a_, b_ = pts[max(0, k - 1)], pts[k]               # k = 0: the open itself (gaps through levels)
            mv = sd * (b_ - a_)
            if (k == 0 or mv < 0) and adverse_leg(a_, b_, k == 0): return True
            if (k == 0 or mv > 0) and favourable_leg(a_, b_): return True
            p['best'] = max(p['best'], b_) if sd == 1 else min(p['best'], b_)
            # stops ratchet after each LEG of the path (a zero-length leg too, e.g. open == high), not at the bare open:
            # a trail moved only by the fresh ATR (atr[i-1]) takes effect after the first leg, as before FBL-BT01
            if k: ratchet(m, p, atr)
        return False

    for i in idx:
        if days[i] != day:
            day, halted = days[i], False
            day_start = eq + sum(p['side'] * (book.arr[s]['c'][i - 1] - p['avg']) * p['qty'] for sl in SL for s, p in sl['pos'].items())
        # ---- fills of pending entries
        for sl in SL:
            m, cfg = sl['m'], sl['cfg']
            sl_eq = eq * cfg['share']
            for s, side in list(sl['pend'].items()):
                if len(sl['pos']) >= cfg['max_pos'] or halted: break
                a = book.arr[s]; atr = a['atr'][i - 1]
                if entry_order == 'maker':
                    lim = a['c'][i - 1]
                    if (a['l'][i] < lim) if side == 1 else (a['h'][i] > lim):
                        px, fee = (min(lim, a['o'][i]) if side == 1 else max(lim, a['o'][i])), FM
                    elif maker_fallback:
                        px, fee = a['o'][i] * (1 + side * SLIP), FEE
                    else:
                        continue
                else:
                    px, fee = a['o'][i] * (1 + side * SLIP), FEE
                open_pos(sl, s, side, px, atr, i, sl_eq, fee)
            sl['pend'] = {}
            # trailing entries: enter when price rebounds dev_atr ATR from the extreme since the signal (candle path)
            for s, te in list(sl['tpend'].items()):
                if i > te['until'] or halted or s in sl['pos'] or len(sl['pos']) >= cfg['max_pos'] or \
                        (RR['btc_breaker'] and i <= breaker_until):
                    del sl['tpend'][s]; continue
                a = book.arr[s]; sd = te['side']; o, h, l, c = a['o'][i], a['h'][i], a['l'][i], a['c'][i]
                pts = [o, l, h, c] if c >= o else [o, h, l, c]
                d, ext, fill = te['dev'] * te['atr'], te['ext'], None
                for k, pt in enumerate(pts):
                    lvl = ext + sd * d
                    if sd * (pt - lvl) >= 0:
                        fill = pt if k == 0 else lvl; break
                    ext = min(ext, pt) if sd == 1 else max(ext, pt)
                te['ext'] = ext
                if fill is None: continue
                del sl['tpend'][s]
                p = open_pos(sl, s, sd, fill * (1 + sd * SLIP), te['atr'], i, eq * cfg['share'], FEE)
                if p is not None: p['skip_i'] = i                 # filled mid-candle: managed from the next candle on
        # ---- manage open positions
        for sl in SL:
            m, cfg = sl['m'], sl['cfg']
            RUN = m.get('runner'); TTP = m.get('ttp'); TPS = sl['tps']
            for s in list(sl['pos']):
                p = sl['pos'][s]; a = book.arr[s]; sd = p['side']
                if p.get('skip_i') == i: continue
                # ATR of the last CLOSED candle: candle i's own ATR contains its future high/low (the live engine cannot know it)
                o, h, l, c, atr = a['o'][i], a['h'][i], a['l'][i], a['c'][i], a['atr'][i - 1]
                eq -= p['qty'] * c * FPB; p['realized'] -= p['qty'] * c * FPB
                # 1) gap at the open already through the stop: filled at the open, nothing else (no adds)
                if (o <= p['stop']) if sd == 1 else (o >= p['stop']):
                    close(sl, s, p, o, 1, i, 'stop'); del sl['pos'][s]; continue
                # 2-5) every intrabar event (stop, DCA fills, basket TP, pyramid adds, tp1 / ladder / tp_r, breakeven and
                # trailing ratchets) in the order of the declared price path - see path_points() and walk_path()
                if walk_path(sl, s, p, i, o, h, l, c, atr): del sl['pos'][s]; continue
                # 6) signal / time exits at close
                ex = sl['sigs'][s]['lx' if sd == 1 else 'sx'][i]
                if RUN:
                    trend_ok = (c > a['e50'][i] and a['st'][i] > 0) if sd == 1 else (c < a['e50'][i] and a['st'][i] < 0)
                    winning = sd * (c - p['avg']) > 0
                    if winning and trend_ok: ex = False                       # winners in a healthy trend are never exited by the signal
                    elif winning and RUN.get('trend_exit') and not trend_ok: ex = True
                if ex or (m.get('max_bars') and not RUN and i - p['i'] >= m['max_bars']):
                    close(sl, s, p, c, 1, i, 'signal' if ex else 'time'); del sl['pos'][s]
        # ---- liquidation: cross-margin equity at every open position's adverse extreme vs maintenance margin
        if maint_margin:
            allp = [(sl, s, p) for sl in SL for s, p in sl['pos'].items()]
            if allp:
                adv_px = {id(p): (book.arr[s]['l'][i] if p['side'] == 1 else book.arr[s]['h'][i]) for _, s, p in allp}
                upl = sum(p['side'] * (adv_px[id(p)] - p['avg']) * p['qty'] for _, s, p in allp)
                notl = sum(p['qty'] * adv_px[id(p)] for _, s, p in allp)
                if eq + upl < maint_margin * notl:
                    liqs += 1
                    for sl, s, p in allp:
                        close(sl, s, p, adv_px[id(p)], 1, i, 'liquidated'); del sl['pos'][s]
                    halted = True
        up_now = sum(p['side'] * (book.arr[s]['c'][i] - p['avg']) * p['qty'] for sl in SL for s, p in sl['pos'].items())
        if (eq + up_now) / day_start - 1 <= -daily_halt: halted = True                 # same rule as live: includes open P&L
        # ---- BTC circuit breaker (enforce): pause entries for `hours`, optionally move winners to breakeven
        if RR['btc_breaker'] and MV is not None and MV[i] > RR['btc_breaker'].get('pct', 5.0):
            was = i <= breaker_until
            breaker_until = i + max(1, int(np.ceil(RR['btc_breaker'].get('hours', 4) * 3600 / bar_sec)))
            if RR['btc_breaker'].get('tighten') and not was:
                for sl in SL:
                    for s, p in sl['pos'].items():
                        sd = p['side']; be = p['avg'] * (1 + sd * BE_BUF); c = book.arr[s]['c'][i]
                        if sd * (c - be) > 0: p['stop'] = max(p['stop'], be) if sd == 1 else min(p['stop'], be)
        # ---- new signals -> fill next candle
        if not halted and i + 1 < len(T):
            for sl in SL:
                for s in sl['syms']:
                    if s in sl['pos'] or s in sl['tpend']: continue
                    g = sl['sigs'][s]
                    cfg = sl['cfg']
                    if cfg.get('hours') is not None and pd.Timestamp(T[i]).hour not in cfg['hours']: continue
                    if sl['volr'] and sl['volr'][s][i] > cfg['vol_max_pct']: continue
                    side = 1 if g['le'][i] else (-1 if g['se'][i] else 0)
                    if not side: continue
                    if RG is not None or sl['pg'] or RR['btc_breaker']:
                        why = entry_filters(sl, s, side, i)
                        if why: blocked[why] = blocked.get(why, 0) + 1; continue
                    te = cfg.get('trail_entry')
                    if te and te.get('dev_atr'):
                        sl['tpend'][s] = dict(side=side, ext=book.arr[s]['c'][i], atr=book.arr[s]['atr'][i],
                                              dev=te['dev_atr'], until=i + int(te.get('max_bars', 3)))
                    else:
                        sl['pend'][s] = side
        up = sum(p['side'] * (book.arr[s]['c'][i] - p['avg']) * p['qty'] for sl in SL for s, p in sl['pos'].items())
        curve.append(eq + up)
        if G:
            mtm = eq + up; peak_mtm = max(peak_mtm, mtm)
            gmult = _gov_step(G, mtm, peak_mtm, (mtm / start - 1) * 100, (1 - mtm / peak_mtm) * 100 if peak_mtm > 0 else 0)
        if eq + up <= start * 0.02:
            break
    cv = pd.Series(curve, index=pd.to_datetime(T[idx[:len(curve)]]))
    cv.attrs['liquidations'] = liqs; cv.attrs['blocked'] = blocked
    n_feas = FEAS['executed'] + sum(FEAS['skipped'].values())
    FEAS['executable_pct'] = round(FEAS['executed'] / n_feas * 100, 1) if n_feas else None
    cv.attrs['feasibility'] = FEAS
    return pd.DataFrame(trades), cv


norm_tps = S.norm_tps


def martingale_risk(mgmt, atr_pct, fee=FEE, slip=SLIP):
    """Worst case of a DCA / martingale basket, per unit of the slot's risk (risk_usd = 1).
    mgmt: the slot's management dict with 'dca' {n, step_atr, scale, tp_atr, stop_atr}; atr_pct: ATR as a fraction of price.
    Returns dict(worst_loss_r: loss if every safety order fills and the basket stop is hit, incl. fees and slippage, in R;
                 max_notional_r: full-basket notional divided by the slot's risk in USD (x risk% = leverage of the slot capital);
                 first_notional_r, depth_pct (entry -> stop distance in %), avg_entry_pct (basket average vs entry, %), weights).
    A basket without a hard stop (stop_atr) is refused."""
    dc = (mgmt or {}).get('dca')
    if not dc: raise ValueError('not a DCA slot')
    if not dc.get('stop_atr') or dc['stop_atr'] <= 0: raise ValueError('a DCA basket needs a hard basket stop (stop_atr)')
    n, scale = int(dc.get('n', 3)), float(dc.get('scale', 1.5))
    if not (1 <= n <= 8 and 1.0 <= scale <= 3.0): raise ValueError('DCA: n 1-8 and scale 1-3')
    lv = [1 - k * dc['step_atr'] * atr_pct for k in range(n + 1)]
    if lv[-1] <= 0: raise ValueError('safety orders reach a zero price')
    w = [scale ** k for k in range(n + 1)]
    stop = lv[-1] - dc['stop_atr'] * atr_pct
    base = 1.0 / sum(wk * abs(lk - stop) for wk, lk in zip(w, lv))
    qty = base * sum(w)
    notional = base * sum(wk * lk for wk, lk in zip(w, lv))
    avg = notional / qty
    fees = notional * fee + qty * stop * (fee + slip)
    return dict(worst_loss_r=round(1.0 + fees, 3), max_notional_r=round(notional, 2), first_notional_r=round(base * lv[0], 2),
                depth_pct=round((1 - stop) * 100, 2), avg_entry_pct=round((avg - 1) * 100, 2), weights=[round(x, 3) for x in w],
                last_order_share=round(w[-1] / sum(w), 3))


def stats(tr, cv, start=500.0, split='2025-10-01'):
    if cv.empty: return {}
    dd = (cv / cv.cummax() - 1).min()
    n = len(tr)
    gl = -tr.pnl[tr.pnl < 0].sum() if n else 0
    r = cv.resample('1D').last().pct_change().dropna()
    mo = cv.resample('ME').last().pct_change().dropna()
    h1, h2 = cv[cv.index < split], cv[cv.index >= split]
    ret = lambda x: (x.iloc[-1] / x.iloc[0] - 1) * 100 if len(x) > 1 else 0.0
    return dict(end=round(cv.iloc[-1], 0), ret=round((cv.iloc[-1] / start - 1) * 100, 1), maxdd=round(dd * 100, 1),
                sharpe=round(r.mean() / r.std() * np.sqrt(365), 2) if r.std() > 0 else 0.0, trades=n,
                win=round((tr.pnl > 0).mean() * 100, 1) if n else 0,
                pf=round(tr.pnl[tr.pnl > 0].sum() / gl, 2) if gl > 0 else None,
                y1=round(ret(h1), 1), y2=round(ret(h2), 1), worst_month=round(mo.min() * 100, 1) if len(mo) else 0,
                busted=bool(cv.iloc[-1] <= start * 0.05),
                liquidations=int(cv.attrs.get('liquidations', tr.loc[tr.why == 'liquidated', 'i_out'].nunique() if n and 'why' in tr else 0)))
