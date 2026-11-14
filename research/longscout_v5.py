"""LONGSCOUT-V5: six new research-only 4h candidates from families not yet tried (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults where one
exists); none tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py.

S5A s5_high_beta_btc_down_4h      short BTC falling (R1); coin's 30-day (180-bar) beta to BTC >= 1.2; 4h EMA20 crosses
                                        below EMA50. Short the coins that fall hardest when BTC falls.
L5A l5_volume_dry_pullback_4h     long  coin rising (R1); three red bars on falling volume (last one < 0.7x its 42-bar
                                        mean), then a green bar closing above the prior bar's high.
R5A r5_ichimoku_cloud_4h          both  Ichimoku (9/26/52): close crosses above the cloud with Tenkan > Kijun -> long;
                                        mirror -> short; exit on a close back through the Kijun. Ungated. Changed
                                        hypothesis vs CP-Z3 (1h).
L5B l5_anchored_vwap_reclaim_4h   long  VWAP anchored at the lowest low of the last 90 bars; close crosses above it,
                                        coin not falling (R1).
R5B r5_kama_flip_4h               both  Kaufman adaptive MA (10, 2, 30) turns up with efficiency ratio >= 0.3 -> long;
                                        turns down -> short; exit on the opposite turn. Ungated.
S5B s5_btc_down_bounce_fail_4h    short BTC falling (R1), coin EMA50 < EMA200; the bar rallies into the EMA50 and closes
                                        red below it (first such bar). Changed hypothesis vs SS1 (daily, coin regime).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _first(x):
    x = S.X(x)
    return x & ~x.shift(fill_value=False)


def _btc_c(d):
    f = L1._BTC['f']
    return pd.Series(f.c.values, index=pd.to_datetime(f.t)).reindex(pd.to_datetime(d.t)).values


def sig_high_beta_btc_down(d, p, ctx):
    br = L1.btc_regime(d)
    rc = d.c.pct_change(); rb = pd.Series(_btc_c(d), index=d.index).pct_change()
    n = p['beta_n']
    beta = rc.rolling(n, min_periods=n).cov(rb) / rb.rolling(n, min_periods=n).var()
    se = S.xup(d.e50, d.e20) & (beta >= p['beta']) & (br == -1)
    return dict(le=_z(d), se=S.X(se), lx=_z(d), sx=_z(d))


def sig_volume_dry_pullback(d, p, ctx):
    cr = L1.coin_regime(d)
    red = d.c < d.o
    three_red = red.shift(1) & red.shift(2) & red.shift(3)
    vdec = (d.v.shift(3) > d.v.shift(2)) & (d.v.shift(2) > d.v.shift(1))
    dry = d.vol_ratio.shift(1) < p['dry']
    le = S.X(three_red) & vdec & dry & (d.c > d.o) & (d.c > d.h.shift()) & (cr == 1)
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def sig_ichimoku_cloud(d, p, ctx):
    mid = lambda k: (d.h.rolling(k).max() + d.l.rolling(k).min()) / 2
    ten, kij = mid(p['t']), mid(p['k'])
    sa = ((ten + kij) / 2).shift(p['k']); sb = mid(p['b']).shift(p['k'])
    top, bot = np.maximum(sa, sb), np.minimum(sa, sb)
    le = S.xup(d.c, top) & (ten > kij)
    se = S.xup(-d.c, -bot) & (ten < kij)
    return dict(le=S.X(le & ~se), se=S.X(se & ~le), lx=S.X(d.c < kij), sx=S.X(d.c > kij))


def sig_anchored_vwap_reclaim(d, p, ctx):
    cr = L1.coin_regime(d)
    tp = ((d.h + d.l + d.c) / 3).values; v = d.v.values; l = d.l.values; c = d.c.values
    n, k = len(d), p['anchor_n']
    av = np.full(n, np.nan)
    for i in range(k - 1, n):
        a = i - k + 1 + int(np.argmin(l[i - k + 1:i + 1]))
        vv = v[a:i + 1].sum()
        if vv > 0: av[i] = (tp[a:i + 1] * v[a:i + 1]).sum() / vv
    av = pd.Series(av, index=d.index)
    le = (d.c > av) & (d.c.shift() <= av.shift()) & np.isin(cr, (0.0, 1.0))
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def _kama(c, n, fast, slow):
    ch = (c - c.shift(n)).abs(); vol = c.diff().abs().rolling(n).sum()
    er = (ch / vol.replace(0, np.nan)).fillna(0)
    sc = (er * (2 / (fast + 1) - 2 / (slow + 1)) + 2 / (slow + 1)) ** 2
    cv, scv = c.values, sc.values
    k = np.full(len(c), np.nan)
    for i in range(n, len(c)):
        k[i] = cv[i] if np.isnan(k[i - 1]) else k[i - 1] + scv[i] * (cv[i] - k[i - 1])
    return pd.Series(k, index=c.index), er


def sig_kama_flip(d, p, ctx):
    k, er = _kama(d.c, p['n'], p['fast'], p['slow'])
    up = (k > k.shift()) & (k.shift() <= k.shift(2))
    dn = (k < k.shift()) & (k.shift() >= k.shift(2))
    ok = er >= p['er']
    return dict(le=S.X(up & ok), se=S.X(dn & ok), lx=S.X(dn), sx=S.X(up))


def sig_btc_down_bounce_fail(d, p, ctx):
    br = L1.btc_regime(d)
    se = (br == -1) & (d.e50 < d.e200) & (d.h >= d.e50) & (d.c < d.e50) & (d.c < d.o)
    se = _first(se)
    dist = np.where(se, np.clip((d.h.values + p['buf_atr'] * d.atr.values) - d.c.values,
                                p['min_atr'] * d.atr.values, p['max_atr'] * d.atr.values), np.nan)
    return dict(le=_z(d), se=se, lx=_z(d), sx=_z(d), atr=dist)


CANDIDATES = {
    's5_high_beta_btc_down_4h': dict(
        name='[RESEARCH] S5A High-beta short in BTC downtrend 4h', style='trend', fn=sig_high_beta_btc_down,
        sides='short', tf='4h', research_only=True, params=dict(beta_n=180, beta=1.2),
        mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
    'l5_volume_dry_pullback_4h': dict(
        name='[RESEARCH] L5A Low-volume pullback resume long 4h', style='pullback', fn=sig_volume_dry_pullback,
        sides='long', tf='4h', research_only=True, params=dict(dry=0.7), mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24)),
    'r5_ichimoku_cloud_4h': dict(
        name='[RESEARCH] R5A Ichimoku cloud breakout L/S 4h', style='trend', fn=sig_ichimoku_cloud,
        sides='both', tf='4h', research_only=True, params=dict(t=9, k=26, b=52), mgmt=dict(stop_atr=2.5, max_bars=120)),
    'l5_anchored_vwap_reclaim_4h': dict(
        name='[RESEARCH] L5B Low-anchored VWAP reclaim long 4h', style='reversal', fn=sig_anchored_vwap_reclaim,
        sides='long', tf='4h', research_only=True, params=dict(anchor_n=90), mgmt=dict(stop_atr=2.0, tp_r=2.5, max_bars=40)),
    'r5_kama_flip_4h': dict(
        name='[RESEARCH] R5B KAMA turn L/S 4h', style='trend', fn=sig_kama_flip,
        sides='both', tf='4h', research_only=True, params=dict(n=10, fast=2, slow=30, er=0.3),
        mgmt=dict(stop_atr=2.5, max_bars=120)),
    's5_btc_down_bounce_fail_4h': dict(
        name='[RESEARCH] S5B BTC-down EMA50 bounce-fail short 4h', style='trend', fn=sig_btc_down_bounce_fail,
        sides='short', tf='4h', research_only=True, params=dict(buf_atr=0.25, min_atr=1.0, max_atr=3.0),
        mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=30)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
