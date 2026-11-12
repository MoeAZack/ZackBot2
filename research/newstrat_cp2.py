"""NEWSTRAT-CP wave 2 (Claude Projects, 2026-11-12). FROZEN before any wave-2 run; wave-1 results were known, so these
are deliberately different families (wide 4h/daily stops), not retunes of wave 1. No parameter tuned.

CP-N6 btc_lead_catchup_4h        both   BTC breaks its 20-bar extreme; enter trend-aligned alts that have not broken yet
CP-N7 capitulation_reversal_1d   both   daily 20-day-extreme spike with a >=2x volume rejection candle, faded
CP-N8 squeeze_breakdown_short_4h short  Bollinger squeeze resolved downward through the 20-bar low while BTC < EMA200
"""
import numpy as np, pandas as pd
import strategies as S

PROTECT = dict(tp1_r=1.0, tp1_frac=0.5, be_r=1.0)
BTC = {}            # set by the runner: {'up': Series(bool, index=t), 'dn': ...} from BTC's own candles


def set_btc(df):
    d = S.indicators(df.drop_duplicates('t').sort_values('t').reset_index(drop=True))
    t = pd.to_datetime(d.t)
    BTC['up'] = pd.Series(S.X(S.xup(d.c, d.hh20)).values, index=t)
    BTC['dn'] = pd.Series(S.X(S.xup(-d.c, -d.ll20)).values, index=t)


def sig_btc_lead(d, p, ctx):
    t = pd.to_datetime(d.t)
    bu = pd.Series(BTC['up'].reindex(t).fillna(False).values, index=d.index)
    bd = pd.Series(BTC['dn'].reindex(t).fillna(False).values, index=d.index)
    le = bu & (d.c < d.hh20) & (d.c > d.e50) & (d.e50 > d.e200)
    se = bd & (d.c > d.ll20) & (d.c < d.e50) & (d.e50 < d.e200)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z)


def sig_capitulation(d, p, ctx):
    rng = (d.h - d.l)
    big = rng >= p['range_atr'] * d.atr.shift()
    vol = d.v >= p['vol_mult'] * d.v.rolling(20, min_periods=20).median().shift()
    pos = (d.c - d.l) / rng.replace(0, np.nan)
    le = big & vol & (d.l < d.ll20) & (pos >= 0.5)
    se = big & vol & (d.h > d.hh20) & (pos <= 0.5)
    dist = np.where(se, (d.h + p['buf_atr'] * d.atr) - d.c, np.where(le, d.c - (d.l - p['buf_atr'] * d.atr), np.nan))
    dist = np.maximum(dist, 0.5 * d.atr.values)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z, atr=dist)


def sig_squeeze_short(d, p, ctx):
    bd = pd.Series(ctx['btc_down'], index=d.index).astype(bool)
    se = bd & (d.c < d.e200) & (d.bbw_pct.shift() <= p['sq_pct']) & (d.c < d.bbl) & (d.c < d.ll20)
    z = pd.Series(False, index=d.index)
    return dict(le=z, se=S.X(se), lx=z, sx=z)


CANDIDATES = {
    'cp_n6_btc_lead_catchup_4h': dict(
        name='[RESEARCH] CP-N6 BTC-lead alt catch-up 4h', style='cross_asset_diffusion', fn=sig_btc_lead, sides='both',
        tf='4h', research_only=True, params={}, exclude=['BTCUSDT'],
        mgmt=dict(stop_atr=2.0, tp_r=3.0, max_bars=30, **PROTECT)),
    'cp_n7_capitulation_reversal_1d': dict(
        name='[RESEARCH] CP-N7 Capitulation / blow-off reversal daily', style='mean_reversion', fn=sig_capitulation,
        sides='both', tf='1d', research_only=True, params=dict(range_atr=2.0, vol_mult=2.0, buf_atr=0.25),
        mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=10, **PROTECT)),
    'cp_n8_squeeze_breakdown_short_4h': dict(
        name='[RESEARCH] CP-N8 Squeeze breakdown short 4h', style='breakout', fn=sig_squeeze_short, sides='short',
        tf='4h', research_only=True, params=dict(sq_pct=0.10),
        mgmt=dict(stop_atr=2.0, tp_r=3.0, max_bars=30, **PROTECT)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
