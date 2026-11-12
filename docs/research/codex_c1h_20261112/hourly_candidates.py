"""Fixed, research-only C1H hypotheses. Signals are evaluated on closed bars."""
import numpy as np
import pandas as pd
import strategies as S

_PANEL = {}

def set_panel(raw):
    """Install the raw symbol -> candle-frame panel for BTC relative momentum."""
    global _PANEL
    _PANEL = raw

def _out(d, le, se):
    false = pd.Series(False, index=d.index)
    return dict(le=le.fillna(False).astype(bool), se=se.fillna(False).astype(bool), lx=false.copy(), sx=false.copy())

def h1(d, p, ctx):
    up = (d.e50 > d.e200) & (d.e200 > d.e200.shift(24))
    dn = (d.e50 < d.e200) & (d.e200 < d.e200.shift(24))
    return _out(d, up & (d.c.shift() < d.e20.shift()) & (d.l.shift() <= d.e20.shift()) & (d.c > d.e20) & (d.c > d.h.shift()),
                dn & (d.c.shift() > d.e20.shift()) & (d.h.shift() >= d.e20.shift()) & (d.c < d.e20) & (d.c < d.l.shift()))

def h2(d, p, ctx):
    hi = d.h.rolling(12, min_periods=12).max().shift()
    lo = d.l.rolling(12, min_periods=12).min().shift()
    tight = (hi - lo) <= 4 * d.atr.shift()
    volume = d.v >= 1.25 * d.v.rolling(24, min_periods=24).median().shift()
    return _out(d, tight & volume & (d.e50 > d.e200) & (d.c > hi) & (d.c.shift() <= hi.shift()),
                tight & volume & (d.e50 < d.e200) & (d.c < lo) & (d.c.shift() >= lo.shift()))

def h3(d, p, ctx):
    flat = (d.adx < 20) & (((d.e50 - d.e50.shift(24)).abs() / d.atr) < 1)
    lower, upper = d.e50 - 2 * d.atr, d.e50 + 2 * d.atr
    return _out(d, flat & (d.c.shift() < lower.shift()) & (d.c > lower),
                flat & (d.c.shift() > upper.shift()) & (d.c < upper))

def h4(d, p, ctx):
    r = d.rsi2
    return _out(d, (d.e50 > d.e200) & (d.c > d.e200) & (r.shift().rolling(3, min_periods=3).min() < 10) & (r > 30) & (r.shift() <= 30),
                (d.e50 < d.e200) & (d.c < d.e200) & (r.shift().rolling(3, min_periods=3).max() > 90) & (r < 70) & (r.shift() >= 70))

def h5(d, p, ctx):
    body, span = (d.c - d.o).abs(), d.h - d.l
    ok = (body >= d.atr) & (body <= 3 * d.atr) & (d.v >= 2 * d.v.rolling(24, min_periods=24).median().shift()) & (span > 0)
    return _out(d, ok & (d.c > d.o) & (d.c >= d.l + .8 * span) & (d.e50 > d.e200),
                ok & (d.c < d.o) & (d.c <= d.l + .2 * span) & (d.e50 < d.e200))

def h6(d, p, ctx):
    symbol = str(ctx.get('symbol', '')).upper().replace('/', '').replace('-', '')
    false = pd.Series(False, index=d.index)
    if symbol.startswith('BTC'):
        return _out(d, false, false)
    btc = ctx.get('btc')
    if btc is None:
        btc = next((v for k, v in _PANEL.items() if str(k).upper().replace('/', '').replace('-', '').startswith('BTC')), None)
    if btc is None:
        return _out(d, false, false)
    # Match the exact closed-bar timestamps: never forward-fill a missing BTC bar.
    if 't' in d and 't' in btc:
        b = pd.Series(btc.c.to_numpy(), index=pd.to_datetime(btc.t, utc=True))
        if not b.index.is_unique:
            raise ValueError('Duplicate BTC candle timestamp')
        bc = pd.Series(b.reindex(pd.to_datetime(d.t, utc=True)).to_numpy(), index=d.index)
    else:
        bc = btc.c.reindex(d.index)
    rel = (d.c / d.c.shift(24) - 1) - (bc / bc.shift(24) - 1)
    hi = d.h.rolling(6, min_periods=6).max().shift()
    lo = d.l.rolling(6, min_periods=6).min().shift()
    volume = d.v >= 1.25 * d.v.rolling(24, min_periods=24).median().shift()
    return _out(d, (rel > .01) & (d.e50 > d.e200) & (d.c > d.e50) & (d.c > hi) & (d.c.shift() <= hi.shift()) & volume,
                (rel < -.01) & (d.e50 < d.e200) & (d.c < d.e50) & (d.c < lo) & (d.c.shift() >= lo.shift()) & volume)

_specs = [
 ('c1h_h1_ema_pullback', h1, 'EMA pullback resumption', 'trend', 2, 3, 24),
 ('c1h_h2_compression_breakout', h2, 'Compression breakout', 'breakout', 2, 3, 24),
 ('c1h_h3_keltner_reclaim', h3, 'Flat-market Keltner reclaim', 'mean_reversion', 2, 2, 12),
 ('c1h_h4_rsi2_reset', h4, 'RSI2 trend reset', 'trend', 2, 2.5, 18),
 ('c1h_h5_volume_impulse', h5, 'Volume impulse', 'momentum', 2.5, 3, 24),
 ('c1h_h6_btc_relative', h6, 'BTC-relative momentum', 'momentum', 2, 3, 24),
]
CANDIDATES = {}
for key, fn, name, style, stop, target, bars in _specs:
    CANDIDATES[key] = dict(name='[RESEARCH] ' + name + ' 1h', desc=name + ' fixed C1H hypothesis.', style=style,
        fn=fn, sides='both', tf='1h', research_only=True, params={},
        mgmt=dict(stop_atr=stop, tp_r=target, max_bars=bars, be_r=0, trail_atr=0,
                  prot=dict(at_r=1, trail_atr=2)))


def register():
    """Register these research candidates explicitly in the local strategy process."""
    S.STRATEGIES.update(CANDIDATES)
