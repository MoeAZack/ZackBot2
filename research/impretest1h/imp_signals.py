"""IMP-RETEST-1H signal state machine (frozen ruling 1a1effaa, P2). Research only, default OFF.
Impulse bar i: ATR14_i > 0, ATR14_i <= |c-o| <= 3*ATR14_i, v >= 2*median(previous 24 consecutive volumes, excl. i),
range > 0, directional close in the outer 20% (bull c >= l+.8(h-l); bear c <= h-.8(h-l)). Stores dir, M=(o+c)/2, O=o.
One pending setup per coin (oldest kept, newer impulses ignored). Bars i+1..i+12: up setup cancels on close < O, else
triggers on low <= M, close >= M, close > open (down mirrors); cancel wins; equality at O does not cancel; expires after
i+12; a missing/invalid bar invalidates. Trigger consumes the setup (even if later refused); the resolving bar cannot
start a new setup. Trigger bar j is the signal bar: entry j+1 open, stop 3*ATR14_j (simulator)."""
import numpy as np, pandas as pd

LOOK, VOLX, VOLWIN = 12, 2.0, 24
HOUR = pd.Timedelta(hours=1)


def grid(d, atr):
    """d: frame indexed by hourly timestamps (o,h,l,c,v), possibly with clock gaps; returns a complete-grid frame + valid mask."""
    ix = pd.date_range(d.index[0], d.index[-1], freq='h')
    g = d.reindex(ix); g['atr'] = pd.Series(np.asarray(atr, dtype=float), index=d.index).reindex(ix)
    ok = (np.isfinite(g[['o', 'h', 'l', 'c', 'v']]).all(axis=1) & (g[['o', 'h', 'l', 'c']] > 0).all(axis=1) & (g.v >= 0)
          & (g.h >= g[['o', 'c']].max(axis=1)) & (g.l <= g[['o', 'c']].min(axis=1)))
    return g, ok


def impulses(g, ok):
    body = g.c - g.o; rng = g.h - g.l
    vmed = g.v.where(ok).shift(1).rolling(VOLWIN, min_periods=VOLWIN).median()
    base = ok & (g.atr > 0) & (body.abs() >= g.atr) & (body.abs() <= 3 * g.atr) & (g.v >= VOLX * vmed) & (rng > 0)
    bull = base & (body > 0) & (g.c >= g.l + .8 * rng)
    bear = base & (body < 0) & (g.c <= g.h - .8 * rng)
    return bull.fillna(False).to_numpy(), bear.fillna(False).to_numpy()


def run_state(g, ok):
    """Returns list of events dict(kind, i, j, side) for kind in trigger/cancel/expire/invalid, in bar order."""
    bull, bear = impulses(g, ok)
    o, c, h, l = g.o.to_numpy(), g.c.to_numpy(), g.h.to_numpy(), g.l.to_numpy(); okv = ok.to_numpy()
    ev, pend = [], None
    for k in range(len(g)):
        resolved = False
        if pend is not None:
            i, sd, M, O = pend
            if not okv[k]:
                ev.append(dict(kind='invalid', i=i, j=k, side=sd)); pend = None; resolved = True
            else:
                if sd > 0 and c[k] < O or sd < 0 and c[k] > O:
                    ev.append(dict(kind='cancel', i=i, j=k, side=sd)); pend = None
                elif sd > 0 and l[k] <= M and c[k] >= M and c[k] > o[k] or sd < 0 and h[k] >= M and c[k] <= M and c[k] < o[k]:
                    ev.append(dict(kind='trigger', i=i, j=k, side=sd)); pend = None
                elif k - i >= LOOK:
                    ev.append(dict(kind='expire', i=i, j=k, side=sd)); pend = None
                resolved = pend is None
        if pend is None and not resolved and okv[k] and (bull[k] or bear[k]):
            sd = 1 if bull[k] else -1
            pend = (k, sd, (o[k] + c[k]) / 2, o[k])
    return ev
