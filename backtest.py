"""Portfolio backtester for ZackBot strategies (same rules the live engine uses).

Fills: entries at next candle open; stop checked before anything else in a candle (conservative);
targets/adds fill at their trigger price; signal exits at the candle close.
Costs: taker fee + slippage per side, funding cost on every open position.
"""
import numpy as np
import pandas as pd
import strategies as S

FEE, SLIP, FUND_PER_BAR = 0.0005, 0.0002, 0.00005
BE_BUF = 0.0015        # breakeven stop sits just past entry so fees are covered     # 0.01%/8h on 4h candles
MIN_NOTIONAL = {'BTCUSDT': 50, 'ETHUSDT': 20, 'LINKUSDT': 20}


class Book:
    """Shared data: indicator frames + cross-sectional context for a set of symbols."""
    def __init__(self, raw):      # raw: {sym: DataFrame t,o,h,l,c,v}
        n = min(len(d) for d in raw.values())
        self.syms = list(raw)
        self.d = {s: S.indicators(raw[s].iloc[-n:].reset_index(drop=True)) for s in raw}
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


def run(book, sleeves, start=500.0, max_lev=10.0, daily_halt=0.08, t0=None, t1=None, warmup=220, fund_per_bar=None):
    FPB = FUND_PER_BAR if fund_per_bar is None else fund_per_bar
    """sleeves: list of dict(key, share, risk, max_pos, sides(optional: long/short/both), mgmt(optional overrides),
    symbols(optional subset), params(optional))."""
    syms_all = book.syms
    T = book.t.values
    idx = np.arange(warmup, len(T))
    if t0 is not None: idx = idx[T[idx] >= np.datetime64(t0)]
    if t1 is not None: idx = idx[T[idx] < np.datetime64(t1)]
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
        SL.append(dict(cfg=sl, m=m, syms=syms, sigs=sigs, pos={}, pend={}, volr=volr, hist=[]))

    eq, trades, curve = start, [], []
    day, day_start, halted = None, eq, False
    days = pd.to_datetime(T).date

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

    for i in idx:
        if days[i] != day: day, day_start, halted = days[i], eq, False
        # ---- fills of pending entries
        for sl in SL:
            m, cfg = sl['m'], sl['cfg']
            sl_eq = eq * cfg['share']
            for s, side in list(sl['pend'].items()):
                if len(sl['pos']) >= cfg['max_pos'] or halted: break
                a = book.arr[s]; atr = a['atr'][i - 1]
                px = a['o'][i] * (1 + side * SLIP)
                risk_usd = sl_eq * cfg['risk'] * kelly_mult(sl)
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
                cap = max(0.0, max_lev * sl_eq - notional(sl, i)) / px
                qty = min(qty, cap)
                if qty * px < MIN_NOTIONAL.get(s, 5): continue
                eq -= qty * px * FEE
                p = dict(side=side, qty=qty, q0=qty, avg=px, e0=px, stop=stop, R=stop_dist, risk=risk_usd, i=i,
                         best=px, realized=-qty * px * FEE, tp1=False, adds=0, dca=0, atr0=atr)
                if 'pyramid' in m: p['next_add'] = px + side * m['pyramid']['step_r'] * stop_dist
                if 'dca' in m:
                    p['levels'] = lv[1:]; p['w'] = w[1:]
                    p['tp'] = px + side * m['dca']['tp_atr'] * atr
                sl['pos'][s] = p
            sl['pend'] = {}
        # ---- manage open positions
        for sl in SL:
            m, cfg = sl['m'], sl['cfg']
            RUN = m.get('runner')
            for s in list(sl['pos']):
                p = sl['pos'][s]; a = book.arr[s]; sd = p['side']
                o, h, l, c, atr = a['o'][i], a['h'][i], a['l'][i], a['c'][i], a['atr'][i]
                eq -= p['qty'] * c * FPB; p['realized'] -= p['qty'] * c * FPB
                fav, adv = (h, l) if sd == 1 else (l, h)          # favourable / adverse extreme
                hit = lambda lvl, x: (x >= lvl) if sd == 1 else (x <= lvl)
                # 1) stop
                if (o <= p['stop']) if sd == 1 else (o >= p['stop']):
                    close(sl, s, p, o, 1, i, 'stop'); del sl['pos'][s]; continue
                if (l <= p['stop']) if sd == 1 else (h >= p['stop']):
                    close(sl, s, p, p['stop'], 1, i, 'stop'); del sl['pos'][s]; continue
                # 2) DCA safety orders + basket TP
                if 'dca' in m:
                    while p['dca'] < len(p['levels']) and ((l <= p['levels'][p['dca']]) if sd == 1 else (h >= p['levels'][p['dca']])):
                        lvl = p['levels'][p['dca']]; q = p['q0'] * p['w'][p['dca']]
                        if notional(sl, i) + q * lvl > max_lev * eq * cfg['share']: break
                        p['avg'] = (p['avg'] * p['qty'] + lvl * q) / (p['qty'] + q); p['qty'] += q
                        eq -= q * lvl * FEE; p['realized'] -= q * lvl * FEE; p['dca'] += 1
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
                        if notional(sl, i) + q * lvl > max_lev * eq * cfg['share']: break
                        p['avg'] = (p['avg'] * p['qty'] + lvl * q) / (p['qty'] + q); p['qty'] += q
                        eq -= q * lvl * FEE; p['realized'] -= q * lvl * FEE; p['adds'] += 1
                        p['next_add'] += sd * py['step_r'] * p['R']
                # 4) partial TP / breakeven / full TP
                if m.get('tp1_r') and not p['tp1'] and hit(p['e0'] + sd * m['tp1_r'] * p['R'], fav):
                    close(sl, s, p, p['e0'] + sd * m['tp1_r'] * p['R'], m.get('tp1_frac', 0.5), i, 'tp1'); p['tp1'] = True
                if m.get('be_r') and hit(p['e0'] + sd * m['be_r'] * p['R'], fav):
                    p['stop'] = max(p['stop'], p['avg']) if sd == 1 else min(p['stop'], p['avg'])
                if m.get('tp_r') and not RUN and hit(p['e0'] + sd * m['tp_r'] * p['R'], fav):
                    close(sl, s, p, p['e0'] + sd * m['tp_r'] * p['R'], 1, i, 'tp'); del sl['pos'][s]; continue
                # 5) trailing (chandelier from best price)
                p['best'] = max(p['best'], h) if sd == 1 else min(p['best'], l)
                if m.get('trail_atr'):
                    cand = p['best'] - sd * m['trail_atr'] * atr
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
                # 6) signal / time exits at close
                ex = sl['sigs'][s]['lx' if sd == 1 else 'sx'][i]
                if RUN:
                    trend_ok = (c > a['e50'][i] and a['st'][i] > 0) if sd == 1 else (c < a['e50'][i] and a['st'][i] < 0)
                    winning = sd * (c - p['avg']) > 0
                    if winning and trend_ok: ex = False                       # winners in a healthy trend are never exited by the signal
                    elif winning and RUN.get('trend_exit') and not trend_ok: ex = True
                if ex or (m.get('max_bars') and not RUN and i - p['i'] >= m['max_bars']):
                    close(sl, s, p, c, 1, i, 'signal' if ex else 'time'); del sl['pos'][s]
        if eq / day_start - 1 <= -daily_halt: halted = True
        # ---- new signals -> fill next candle
        if not halted and i + 1 < len(T):
            for sl in SL:
                for s in sl['syms']:
                    if s in sl['pos']: continue
                    g = sl['sigs'][s]
                    cfg = sl['cfg']
                    if cfg.get('hours') is not None and pd.Timestamp(T[i]).hour not in cfg['hours']: continue
                    if sl['volr'] and sl['volr'][s][i] > cfg['vol_max_pct']: continue
                    if g['le'][i]: sl['pend'][s] = 1
                    elif g['se'][i]: sl['pend'][s] = -1
        up = sum(p['side'] * (book.arr[s]['c'][i] - p['avg']) * p['qty'] for sl in SL for s, p in sl['pos'].items())
        curve.append(eq + up)
        if eq + up <= start * 0.02:
            break
    cv = pd.Series(curve, index=pd.to_datetime(T[idx[:len(curve)]]))
    return pd.DataFrame(trades), cv


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
                busted=bool(cv.iloc[-1] <= start * 0.05))
