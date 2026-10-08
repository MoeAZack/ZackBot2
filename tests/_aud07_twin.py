"""AUD-07 twin runner for the product regression tests (helper module, not a test file).

Runs ONE slot on hand-built flat markets through BOTH legacy models with the same injected signals:
- the backtester (backtest.Book with the signal arrays injected, backtest.run);
- the real engine (engine.Engine driven by replay.run_replay; signals injected by replacing strategies.signals, the coin
  identified by its price level).
Returns canonical trades [(sym, side, i_in, i_out, exit, R)] for each, i_out = the candle whose close (or path) exits.
Self-contained on purpose: the golden pack's adapters are being reworked in parallel (#32), these tests must not depend
on their internals.

Market: every symbol is flat at its own price level `px` with wick `w` (true range 2w -> ATR exactly 2w in both models);
declared candles override single bars and the following candles stay flat at the declared close.
"""
import copy
from datetime import timedelta

import numpy as np
import pandas as pd

import backtest as B

T0 = 260                     # first engine decision bar; the backtest starts at the next bar, like replay.run_replay
N = 300
WHY_BT = {'stop': 'stop', 'time': 'time', 'signal': 'signal', 'tp': 'tp'}
WHY_ENG = {'stop': 'stop', 'stop_crossed': 'stop', 'time_exit': 'time', 'exit_signal': 'signal', 'basket_tp': 'tp',
           'take_profit': 'tp'}
CYCLE = {'time_exit', 'exit_signal'}             # decided at a candle close, booked by the next cycle


def market(spec, start='2024-01-01', n=N):
    """spec: {sym: (px, wick, {bar: (o, h, l, c)})} -> {sym: DataFrame t,o,h,l,c,v} (4h candles)."""
    t = pd.date_range(pd.Timestamp(start), periods=n, freq='4h')
    out = {}
    for s, (px, w, bars) in spec.items():
        o = np.full(n, float(px)); c = o.copy(); h = o + w; l = o - w
        last = None
        for j in range(n):
            if j in bars:
                o[j], h[j], l[j], c[j] = map(float, bars[j]); last = c[j]
            elif last is not None:
                o[j] = c[j] = last; h[j] = last + w; l[j] = last - w
        out[s] = pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=1000.0))
    return out


def _sig(raw, signals):
    out = {s: {k: np.zeros(len(d), bool) for k in ('le', 'se', 'lx', 'sx')} for s, d in raw.items()}
    for bar, s, kind in signals:
        out[s][kind][bar] = True
    return out


def run_bt(raw, slot, signals, start=500.0):
    sig = _sig(raw, signals)
    bk = B.Book(copy.deepcopy(raw))
    for s in bk.syms:
        bk._sig[(slot['key'], s, 'None')] = sig[s]
    cfg = [dict(key=slot['key'], share=1.0, risk=slot['risk'], max_pos=slot['max_pos'], symbols=list(slot['symbols']),
                sides=slot['sides'], mgmt=copy.deepcopy(slot['mgmt']),
                **({'trail_entry': dict(slot['trail_entry'])} if slot.get('trail_entry') else {}))]
    syms = list(raw)
    tr, cv = B.run(bk, cfg, start=start, max_lev=10, t0=raw[syms[0]].t[T0 + 1], fund_per_bar=0.0)
    rows = [(r['sym'], int(r['side']), int(r['i_in']), int(r['i_out']), WHY_BT.get(r['why'], r['why']), float(r['R']))
            for r in (tr.to_dict('records') if len(tr) else [])]
    return sorted(rows, key=lambda x: (x[2], x[0])), cv.attrs


def run_engine(raw, slot, signals, start=500.0, entry_delay_s=15, steps=8):
    import engine as E
    import strategies as S
    from replay import run_replay
    sig = _sig(raw, signals)
    level = {s: float(np.median(d.c.values[:T0])) for s, d in raw.items()}

    def ident(d):
        lv = float(np.median(d['c'].values[:50]))
        return min(level, key=lambda s: abs(level[s] - lv))

    def fake_signals(k, d, ctx, params=None, mask_sides=True):
        s = ident(d)
        pos = {tt: j for j, tt in enumerate(raw[s].t.values.astype('datetime64[ns]'))}
        idx = np.array([pos[tt] for tt in pd.to_datetime(d['t']).values.astype('datetime64[ns]')])
        return {f: sig[s][f][idx].copy() for f in ('le', 'se', 'lx', 'sx')}

    sleeve = E.sleeve('G', slot['key'], 1.0, slot['risk'], slot['max_pos'], list(slot['symbols']), sides=slot['sides'],
                      mgmt=copy.deepcopy(slot['mgmt']))
    if slot.get('trail_entry'): sleeve['trail_entry'] = dict(slot['trail_entry'])
    real_signals, real_create = S.signals, E.Engine._create_lot
    extra = entry_delay_s - 15

    def slow_create(self_, *a, **k):             # the entry cycle runs `extra` seconds later than the exit cycle
        f = E.now_utc
        E.now_utc = lambda: f() + timedelta(seconds=extra)
        try:
            return real_create(self_, *a, **k)
        finally:
            E.now_utc = f
    S.signals = fake_signals
    if extra: E.Engine._create_lot = slow_create
    try:
        r = run_replay(copy.deepcopy(raw), [sleeve], T0, steps=steps, start=start)
    finally:
        S.signals, E.Engine._create_lot = real_signals, real_create
    t_index = {pd.Timestamp(ts): k for k, ts in enumerate(raw[list(raw)[0]].t)}

    def bar(ts):
        return t_index[pd.Timestamp(ts).tz_convert('UTC').tz_localize(None).floor('4h')]
    rows = []
    for h in r['history']:
        why = h['exit_reason']
        rows.append((h['symbol'], 1 if h['side'] == 'LONG' else -1, bar(h['opened']),
                     bar(h['closed']) - (1 if why in CYCLE else 0), WHY_ENG.get(why, why), float(h['pnl']) / float(h['risk_usd'])))
    return sorted(rows, key=lambda x: (x[2], x[0])), r


def shape(rows):
    """Everything but R: (sym, side, i_in, i_out, exit)."""
    return [x[:5] for x in rows]
