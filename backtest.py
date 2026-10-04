"""Portfolio backtester for ZackBot strategies (same rules the live engine uses).  ENGINE_VERSION v3

Fills: entries at next candle open; stop checked before anything else in a candle (conservative);
targets/adds fill at their trigger price; signal exits at the candle close.
Costs: taker fee + slippage per side, funding cost on every open position.
"""
import numpy as np
import pandas as pd
import strategies as S

FEE, SLIP, FUND_PER_BAR = 0.0005, 0.0002, 0.00005
FEE_MAKER = 0.0002    # maker fee (post-only limit entries)
BE_BUF = 0.0015
VERSION = 'v3.1'        # breakeven stop sits just past entry so fees are covered     # 0.01%/8h on 4h candles
MIN_NOTIONAL = {'BTCUSDT': 50, 'ETHUSDT': 20, 'LINKUSDT': 20}


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


def run(book, sleeves, start=500.0, max_lev=10.0, daily_halt=0.08, t0=None, t1=None, warmup=220, fund_per_bar=None, pessimistic='path',
        entry_order='market', fee_maker=None, maker_fallback=True, pump_guard=None, risk_rules=None, governor=None,
        maint_margin=0.005, btc1h=None):
    """pessimistic: how a stop tightened during a candle (breakeven / trailing / runner) is checked against that same candle.
    'path' (default): infer the price path from the candle colour; 'worst': always assume the worst order; False: never (old v2).
    v3.1 options (all off by default except the liquidation check, which only fires if margin is actually exhausted):
      entry_order 'maker': limit at the signal close, filled (maker fee) if the next candle trades through it, else
                  at the next open with taker fee (maker_fallback) or skipped
      pump_guard  {'max_candle_atr', 'btc_1h_pct'} global (a sleeve's own 'pump_guard' wins)
      risk_rules  engine RISK_RULES dict; rules in mode 'enforce' are applied (coin_cap, open_risk_cap, correlated_cap,
                  btc_breaker incl. tighten). funding_filter has no historical data here and is ignored
      governor    {'rules': [{'if': 'growth_gte'|'dd_gte', 'value': pct, 'then': {'risk_mult': m}, 'until': 'new_high'|'reset'|None}]}
      maint_margin cross-margin liquidation check per bar (0 = off)
      btc1h       optional BTC 1h candles (t,c) for the 'BTC moved X% in the last hour' rules on a 4h book
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
        m = dict(st['mgmt'], **sl.get('mgmt', {}))
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

    def rule_block(s, side, qty, px, new_risk, i):
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
        if RR['correlated_cap']:
            n, rho = RR['correlated_cap'].get('n', 4), RR['correlated_cap'].get('rho', 0.8)
            same = {s_ for s_, p in allp if p['side'] == side and s_ != s}
            if sum(1 for s_ in same if corr(s, s_, i) > rho) >= n: return 'correlated_cap'
        return None

    def open_pos(sl, s, side, px, atr, i, sl_eq, fee):
        """Size and open a position (same rules as the live engine). Returns the position or None."""
        nonlocal eq
        m, cfg = sl['m'], sl['cfg']
        risk_usd = sl_eq * cfg['risk'] * kelly_mult(sl) * gmult
        if 'dca' in m:
            dc = m['dca']
            lv = [px - side * k * dc['step_atr'] * atr for k in range(dc['n'] + 1)]
            w = [dc['scale'] ** k for k in range(dc['n'] + 1)]
            stop = lv[-1] - side * dc['stop_atr'] * atr
            base = risk_usd / sum(wk * abs(lk - stop) for wk, lk in zip(w, lv))
            qty, stop_dist = base, abs(px - stop)
        else:
            stop_dist = m.get('stop_atr', 2.5) * atr
            qty = risk_usd / stop_dist
            stop = px - side * stop_dist
        qty_raw = qty
        cap = max(0.0, max_lev * sl_eq - notional(sl, i)) / px
        qty = min(qty, cap)
        if qty * px < MIN_NOTIONAL.get(s, 5): return None
        if RR['coin_cap'] or RR['open_risk_cap'] or RR['correlated_cap']:
            why = rule_block(s, side, qty, px, risk_usd * qty / qty_raw, i)
            if why: blocked[why] = blocked.get(why, 0) + 1; return None
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
                o, h, l, c, atr = a['o'][i], a['h'][i], a['l'][i], a['c'][i], a['atr'][i]
                eq -= p['qty'] * c * FPB; p['realized'] -= p['qty'] * c * FPB
                fav, adv = (h, l) if sd == 1 else (l, h)          # favourable / adverse extreme
                hit = lambda lvl, x: (x >= lvl) if sd == 1 else (x <= lvl)
                stop_open = p['stop']
                # 1) stop
                if (o <= p['stop']) if sd == 1 else (o >= p['stop']):
                    close(sl, s, p, o, 1, i, 'stop'); del sl['pos'][s]; continue
                if (l <= p['stop']) if sd == 1 else (h >= p['stop']):
                    close(sl, s, p, p['stop'], 1, i, 'stop'); del sl['pos'][s]; continue
                # 2) DCA safety orders + basket TP
                if 'dca' in m:
                    while p['dca'] < len(p['levels']) and ((l <= p['levels'][p['dca']]) if sd == 1 else (h >= p['levels'][p['dca']])):
                        lvl = p['levels'][p['dca']]; q = p['q0'] * p['w'][p['dca']]
                        lvl = min(lvl, o) if sd == 1 else max(lvl, o)              # gapped through the level -> filled at the open
                        if notional(sl, i) + q * lvl > max_lev * eq * cfg['share']: break
                        p['avg'] = (p['avg'] * p['qty'] + lvl * q) / (p['qty'] + q); p['qty'] += q
                        eq -= q * lvl * FEE; p['realized'] -= q * lvl * FEE; p['dca'] += 1
                        p['qmax'] = max(p['qmax'], p['qty'])
                        p['tp'] = p['avg'] + sd * m['dca']['tp_atr'] * p['atr0']
                    if hit(p['tp'], fav):
                        if RUN:      # runner: bank part at the basket target, keep the rest at breakeven
                            if close(sl, s, p, p['tp'], RUN.get('dca_frac', 1.0), i, 'tp' if RUN.get('dca_frac', 1.0) >= 1 else 'tp1'):
                                del sl['pos'][s]; continue
                            p['tp'] = np.inf * sd; p['tp1'] = True; p['dca'] = len(p['levels'])
                            p['stop'] = max(p['stop'], p['avg'] * (1 + BE_BUF)) if sd == 1 else min(p['stop'], p['avg'] * (1 - BE_BUF))
                            p['e0'], p['R'] = p['avg'], max(p['R'], abs(p['avg'] - p['stop']) or p['R'])
                        else:
                            close(sl, s, p, p['tp'], 1, i, 'tp'); del sl['pos'][s]; continue
                # 3) pyramiding
                if 'pyramid' in m:
                    py = m['pyramid']
                    while p['adds'] < py['n'] and hit(p['next_add'], fav):
                        q = p['q0'] * py['frac']; lvl = p['next_add']
                        lvl = max(lvl, o) if sd == 1 else min(lvl, o)              # gapped through the add level -> filled at the open
                        if notional(sl, i) + q * lvl > max_lev * eq * cfg['share']: break
                        p['avg'] = (p['avg'] * p['qty'] + lvl * q) / (p['qty'] + q); p['qty'] += q
                        eq -= q * lvl * FEE; p['realized'] -= q * lvl * FEE; p['adds'] += 1
                        p['qmax'] = max(p['qmax'], p['qty'])
                        p['next_add'] += sd * py['step_r'] * p['R']
                # 4) partial TP / breakeven / full TP
                if m.get('tp1_r') and not p['tp1'] and hit(p['e0'] + sd * m['tp1_r'] * p['R'], fav):
                    close(sl, s, p, p['e0'] + sd * m['tp1_r'] * p['R'], m.get('tp1_frac', 0.5), i, 'tp1'); p['tp1'] = True
                if TPS:                                                  # take-profit ladder (fractions of the full size)
                    gone = False
                    for k, (r_, f_) in enumerate(TPS):
                        if k in p['tps_done']: continue
                        lvl = p['e0'] + sd * r_ * p['R']
                        if not hit(lvl, fav): break
                        p['tps_done'].add(k)
                        if close(sl, s, p, lvl, min(1.0, p['qmax'] * f_ / p['qty']), i, 'tp_ladder'): gone = True; break
                    if gone: del sl['pos'][s]; continue
                if m.get('be_r') and hit(p['e0'] + sd * m['be_r'] * p['R'], fav):
                    p['stop'] = max(p['stop'], p['avg']) if sd == 1 else min(p['stop'], p['avg'])
                if m.get('tp_r') and not RUN and not TTP and hit(p['e0'] + sd * m['tp_r'] * p['R'], fav):
                    close(sl, s, p, p['e0'] + sd * m['tp_r'] * p['R'], 1, i, 'tp'); del sl['pos'][s]; continue
                # 5) trailing (chandelier from best price)
                p['best'] = max(p['best'], h) if sd == 1 else min(p['best'], l)
                if m.get('trail_atr'):
                    cand = p['best'] - sd * m['trail_atr'] * atr
                    p['stop'] = max(p['stop'], cand) if sd == 1 else min(p['stop'], cand)
                # 5a) trailing take-profit: from at_r R on, the stop follows dev_pct % behind the best price
                if TTP and p['R'] > 0 and sd * (p['best'] - p['e0']) / p['R'] >= TTP.get('at_r', 2.0):
                    cand = p['best'] * (1 - sd * TTP.get('dev_pct', 3.0) / 100)
                    p['stop'] = max(p['stop'], cand) if sd == 1 else min(p['stop'], cand)
                # 5b) runner: ratchet the stop behind the best R reached (breakeven first, then lock profit in steps)
                if RUN and p['R'] > 0:
                    bestR = sd * (p['best'] - p['e0']) / p['R']
                    if bestR >= RUN.get('be_r', 2.0):
                        be = p['avg'] * (1 + sd * BE_BUF)
                        p['stop'] = max(p['stop'], be) if sd == 1 else min(p['stop'], be)
                    lock = np.floor(bestR / RUN.get('step_r', 99)) * RUN.get('step_r', 99) - RUN.get('gap_r', 99)
                    if RUN.get('giveback') and bestR >= RUN.get('gb_from', 4.0):
                        lock = max(lock, bestR * (1 - RUN['giveback']))
                    if lock > 0:
                        lv = p['e0'] + sd * lock * p['R']
                        p['stop'] = max(p['stop'], lv) if sd == 1 else min(p['stop'], lv)
                # 5c) a stop raised inside this candle can already have been hit by this candle's adverse extreme
                # candle path: green = open->low->high->close, red = open->high->low->close. A stop raised at the favourable
                # extreme is hit if the adverse extreme comes AFTER it, or if the close is already beyond it.
                adverse_after = (c < o) if sd == 1 else (c > o)
                crossed = ((l <= p['stop']) if sd == 1 else (h >= p['stop'])) if (pessimistic == 'worst' or adverse_after) else \
                          ((c <= p['stop']) if sd == 1 else (c >= p['stop']))
                if pessimistic and p['stop'] != stop_open and crossed:
                    close(sl, s, p, p['stop'], 1, i, 'stop'); del sl['pos'][s]; continue
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
